from __future__ import annotations

import json
import logging
import math
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .playback import PlaybackEntry, build_playback_sequence
from .utils import require_executable, run_command

FPS = 30
CANONICAL_WIDTH = 1280


@dataclass(frozen=True)
class ResolutionProfile:
    width: int
    height: int
    crf: int


RESOLUTIONS: dict[str, ResolutionProfile] = {
    "480": ResolutionProfile(854, 480, 25),
    "720": ResolutionProfile(1280, 720, 23),
    "1080": ResolutionProfile(1920, 1080, 21),
}

def _transition_blend_expression(transition_type: str, duration: float) -> str:
    ratio = f"min(max(T/{max(0.001, duration):.6f},0),1)"
    normalized = transition_type.lower()
    if normalized in {"wipeleft", "slideleft", "slide-left"}:
        return f"if(lte(X,W*({ratio})),B,A)"
    if normalized in {"wiperight", "slideright", "slide-right"}:
        return f"if(gte(X,W*(1-({ratio}))),B,A)"
    if normalized in {"wipeup", "slideup", "slide-up"}:
        return f"if(lte(Y,H*({ratio})),B,A)"
    if normalized in {"wipedown", "slidedown", "slide-down"}:
        return f"if(gte(Y,H*(1-({ratio}))),B,A)"
    if normalized == "circleopen":
        return f"if(lte(hypot(X-W/2,Y-H/2),hypot(W/2,H/2)*({ratio})),B,A)"
    if normalized == "circleclose":
        return f"if(gte(hypot(X-W/2,Y-H/2),hypot(W/2,H/2)*(1-({ratio}))),B,A)"
    # Fade/dissolve/pixelize/zoom variants use a stable cross-fade fallback.
    return f"A*(1-({ratio}))+B*({ratio})"



def _num(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _escape_filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", r"\'").replace(":", r"\:")


def _escape_color(value: Any, fallback: str = "#ffffff") -> str:
    text = str(value or fallback).strip()
    if text.startswith("#") and len(text) in {4, 7, 9}:
        return text
    return fallback


def _atempo_chain(speed: float) -> str:
    speed = _clamp(speed, 0.25, 4.0)
    factors: list[float] = []
    while speed > 2.0:
        factors.append(2.0)
        speed /= 2.0
    while speed < 0.5:
        factors.append(0.5)
        speed /= 0.5
    factors.append(speed)
    return ",".join(f"atempo={factor:.6f}" for factor in factors)


def probe_media(path: Path) -> dict[str, Any]:
    ffprobe = require_executable("ffprobe")
    result = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
    )
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or []
    duration = _num((payload.get("format") or {}).get("duration"), 0.0)
    return {
        "duration": duration,
        "hasVideo": any(stream.get("codec_type") == "video" for stream in streams),
        "hasAudio": any(stream.get("codec_type") == "audio" for stream in streams),
    }


class FontResolver:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.cache: dict[tuple[str, bool, bool], Path] = {}

    def resolve(self, family: str, bold: bool, italic: bool) -> Path:
        key = (family, bold, italic)
        cached = self.cache.get(key)
        if cached:
            return cached

        local_candidates = [
            self.project_root / "fonts" / f"{family}-{'Bold' if bold else 'Regular'}.ttf",
            self.project_root / "fonts" / f"{family.replace(' ', '')}-{'Bold' if bold else 'Regular'}.ttf",
        ]
        for candidate in local_candidates:
            if candidate.exists():
                self.cache[key] = candidate
                return candidate

        style_parts = []
        if bold:
            style_parts.append("Bold")
        if italic:
            style_parts.append("Italic")
        pattern = family or "DejaVu Sans"
        if style_parts:
            pattern += ":style=" + " ".join(style_parts)

        try:
            result = subprocess.run(
                ["fc-match", "-f", "%{file}", pattern],
                check=True,
                text=True,
                capture_output=True,
            )
            path = Path(result.stdout.strip())
            if path.exists():
                self.cache[key] = path
                return path
        except (OSError, subprocess.CalledProcessError):
            pass

        fallbacks = [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        ]
        for fallback in fallbacks:
            if fallback.exists():
                self.cache[key] = fallback
                return fallback
        raise RuntimeError("Tidak ada font sistem yang dapat dipakai FFmpeg drawtext.")


class FFmpegRenderer:
    def __init__(
        self,
        *,
        project_root: Path,
        work_dir: Path,
        resolution: str,
        preset: str,
        media_library: list[dict[str, Any]],
        media_paths: dict[str, Path],
        music: dict[str, Any],
    ) -> None:
        require_executable("ffmpeg")
        require_executable("ffprobe")
        if resolution not in RESOLUTIONS:
            raise ValueError(f"Resolusi tidak didukung: {resolution}")
        self.profile = RESOLUTIONS[resolution]
        self.resolution = resolution
        self.preset = preset
        self.work_dir = work_dir
        self.project_root = project_root
        self.media_by_id = {
            str(item.get("id") or item.get("mediaId")): item for item in media_library
        }
        self.media_paths = media_paths
        self.music = music or {}
        self.music_bed_path: Path | None = None
        self.fonts = FontResolver(project_root)
        self.probe_cache: dict[Path, dict[str, Any]] = {}
        self.text_counter = 0
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def _probe(self, path: Path) -> dict[str, Any]:
        if path not in self.probe_cache:
            self.probe_cache[path] = probe_media(path)
        return self.probe_cache[path]

    def _encoding_args(self) -> list[str]:
        return [
            "-r",
            str(FPS),
            "-c:v",
            "libx264",
            "-preset",
            self.preset,
            "-crf",
            str(self.profile.crf),
            "-pix_fmt",
            "yuv420p",
            "-g",
            str(FPS * 2),
            "-keyint_min",
            str(FPS * 2),
            "-sc_threshold",
            "0",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
        ]

    def _position(self, element: dict[str, Any]) -> tuple[int, int, int, int]:
        width = max(2, round(self.profile.width * _clamp(_num(element.get("w"), 10), 0.1, 100) / 100))
        height = max(2, round(self.profile.height * _clamp(_num(element.get("h"), 10), 0.1, 100) / 100))
        x = round(self.profile.width * _clamp(_num(element.get("x"), 0), -100, 200) / 100)
        y = round(self.profile.height * _clamp(_num(element.get("y"), 0), -100, 200) / 100)
        return x, y, width, height

    def _write_text_file(self, element: dict[str, Any], width: int, font_size: int) -> Path:
        self.text_counter += 1
        content = str(element.get("content") or "")
        estimated_chars = max(1, round(width / max(4.0, font_size * 0.58)))
        lines: list[str] = []
        for paragraph in content.splitlines() or [""]:
            wrapped = textwrap.wrap(
                paragraph,
                width=estimated_chars,
                replace_whitespace=False,
                drop_whitespace=True,
                break_long_words=True,
            )
            lines.extend(wrapped or [""])
        path = self.work_dir / f"text-{self.text_counter:04d}.txt"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _prepare_music_bed(self, total_duration: float) -> None:
        self.music_bed_path = None
        media_id = self.music.get("mediaId")
        if not media_id:
            return

        path = self.media_paths.get(str(media_id))
        metadata = self.media_by_id.get(str(media_id))
        if not path or not path.exists():
            logging.warning("Music mediaId %s tidak tersedia; render dilanjutkan tanpa musik.", media_id)
            return

        probe = self._probe(path)
        if not probe.get("hasAudio"):
            logging.warning("File musik tidak memiliki audio stream: %s", path)
            return

        source_duration = _num(
            metadata.get("duration") if metadata else probe.get("duration"),
            _num(probe.get("duration"), 0),
        )
        if source_duration <= 0:
            logging.warning("Durasi musik tidak valid; render dilanjutkan tanpa musik: %s", path)
            return

        # Selama video belum melewati akhir lagu, setiap segmen cukup mengambil
        # posisi timeline yang sama dari file asli. loopStart tidak boleh
        # memengaruhi intro.
        if total_duration <= source_duration + 0.001:
            self.music_bed_path = path
            return

        loop_start = _clamp(
            _num(self.music.get("loopStart"), 0.0),
            0.0,
            source_duration,
        )
        loop_duration = source_duration - loop_start
        if loop_duration <= 0.05:
            logging.warning(
                "loopStart musik terlalu dekat dengan akhir; seluruh lagu dipakai sebagai area loop."
            )
            loop_start = 0.0
            loop_duration = source_duration

        loop_samples = max(1, round(loop_duration * 48_000))
        music_bed = self.work_dir / "music-bed.flac"
        filters = [
            "[0:a]aresample=48000,asplit=2[intro_source][loop_source]",
            (
                f"[intro_source]atrim=start=0:end={source_duration:.6f},"
                "asetpts=PTS-STARTPTS[intro]"
            ),
            (
                f"[loop_source]atrim=start={loop_start:.6f}:end={source_duration:.6f},"
                f"asetpts=PTS-STARTPTS,aloop=loop=-1:size={loop_samples}[loop]"
            ),
            (
                f"[intro][loop]concat=n=2:v=0:a=1,atrim=duration={total_duration:.6f},"
                "asetpts=PTS-STARTPTS[aout]"
            ),
        ]
        run_command([
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[aout]",
            "-vn",
            "-c:a",
            "flac",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(music_bed),
        ])
        self.music_bed_path = music_bed
        logging.info(
            "Music bed disiapkan: intro=0-%.3fs loop=%.3f-%.3fs total=%.3fs",
            source_duration,
            loop_start,
            source_duration,
            total_duration,
        )

    def _append_music_input(
        self,
        command: list[str],
        *,
        entry_start: float,
    ) -> tuple[int | None, dict[str, Any] | None]:
        path = self.music_bed_path
        if not path:
            return None, None
        if not path.exists():
            logging.warning("Music bed tidak tersedia; render dilanjutkan tanpa musik: %s", path)
            return None, None
        probe = self._probe(path)
        if not probe.get("hasAudio"):
            logging.warning("File musik tidak memiliki audio stream: %s", path)
            return None, None
        index = self._input_count(command)
        command.extend(["-ss", f"{max(0.0, entry_start):.6f}", "-i", str(path)])
        return index, probe

    @staticmethod
    def _input_count(command: list[str]) -> int:
        return sum(1 for item in command if item == "-i")

    def render_slide(
        self,
        slide: dict[str, Any],
        *,
        duration: float,
        entry_start: float,
        output_path: Path,
    ) -> None:
        duration = max(0.1, duration)
        width, height = self.profile.width, self.profile.height
        # Editor canvas memakai putih sebagai latar default; jangan jatuhkan
        # hasil Bake ke hitam ketika field warna belum ada pada timeline lama.
        background = _escape_color(slide.get("backgroundColor"), "#ffffff")
        command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        command.extend([
            "-f",
            "lavfi",
            "-i",
            f"color=c={background}:s={width}x{height}:r={FPS}:d={duration:.6f}",
        ])
        command.extend([
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={duration:.6f}",
        ])

        filters: list[str] = [
            f"[0:v]setpts=PTS-STARTPTS,format=rgba[v0]",
            f"[1:a]atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[silence]",
        ]
        current_video = "v0"
        audio_labels = ["silence"]
        media_input_data: list[tuple[dict[str, Any], int, Path, dict[str, Any]]] = []

        elements = sorted(
            list(slide.get("elements") or []),
            key=lambda item: _num(item.get("zIndex"), 0),
        )

        for element in elements:
            if element.get("type") != "media":
                continue
            media_id = str(element.get("mediaId") or "")
            path = self.media_paths.get(media_id)
            metadata = self.media_by_id.get(media_id) or {}
            if not path or not path.exists():
                logging.warning("Media %s tidak ditemukan; elemen dilewati.", media_id)
                continue
            media_type = str(element.get("mediaType") or metadata.get("type") or "")
            probe = self._probe(path)
            input_index = self._input_count(command)
            trim_start = max(0.0, _num(element.get("trimStart"), 0.0))
            if media_type == "image":
                command.extend(["-loop", "1", "-framerate", str(FPS), "-i", str(path)])
            else:
                command.extend(["-stream_loop", "-1", "-ss", f"{trim_start:.6f}", "-i", str(path)])
            media_input_data.append((element, input_index, path, probe))

        music_input, _music_probe = self._append_music_input(command, entry_start=entry_start)

        media_cursor = 0
        for element in elements:
            if element.get("type") == "text":
                x, y, element_width, _element_height = self._position(element)
                font_size = max(8, round(_num(element.get("fontSize"), 32) * width / CANONICAL_WIDTH))
                text_path = self._write_text_file(element, element_width, font_size)
                font_path = self.fonts.resolve(
                    str(element.get("fontFamily") or "DejaVu Sans"),
                    bool(element.get("bold")),
                    bool(element.get("italic")),
                )
                opacity = _clamp(_num(element.get("opacity"), 1.0), 0.0, 1.0)
                next_video = f"v_text_{self.text_counter}"
                filters.append(
                    f"[{current_video}]drawtext="
                    f"fontfile='{_escape_filter_path(font_path)}':"
                    f"textfile='{_escape_filter_path(text_path)}':"
                    f"fontcolor={_escape_color(element.get('color'))}:"
                    f"fontsize={font_size}:x={x}:y={y}:"
                    f"alpha={opacity:.6f}:line_spacing={max(1, round(font_size * 0.18))}"
                    f"[{next_video}]"
                )
                if abs(_num(element.get("rotation"), 0)) > 0.01:
                    logging.warning("Rotasi text belum diraster presisi; text dirender tanpa rotasi.")
                current_video = next_video
                continue

            if element.get("type") != "media":
                continue
            media_id = str(element.get("mediaId") or "")
            path = self.media_paths.get(media_id)
            if not path or not path.exists():
                continue
            _input_element, input_index, _path, probe = media_input_data[media_cursor]
            media_cursor += 1
            media_type = str(element.get("mediaType") or "")
            x, y, element_width, element_height = self._position(element)
            opacity = _clamp(_num(element.get("opacity"), 1.0), 0.0, 1.0)
            speed = _clamp(_num(element.get("speed"), 1.0), 0.25, 4.0)
            rotation = math.radians(_num(element.get("rotation"), 0.0))
            rotation_expression = f"{rotation:.10f}"

            if media_type in {"image", "video"} and probe.get("hasVideo"):
                media_label = f"media_{media_cursor}"
                setpts = "PTS-STARTPTS" if media_type == "image" else f"(PTS-STARTPTS)/{speed:.8f}"
                filters.append(
                    f"[{input_index}:v]setpts={setpts},trim=duration={duration:.6f},"
                    f"scale={element_width}:{element_height}:force_original_aspect_ratio=increase,"
                    f"crop={element_width}:{element_height},format=rgba,"
                    f"colorchannelmixer=aa={opacity:.6f},"
                    f"rotate={rotation_expression}:"
                    f"ow=rotw({rotation_expression}):oh=roth({rotation_expression}):c=none"
                    f"[{media_label}]"
                )
                next_video = f"v_media_{media_cursor}"
                filters.append(
                    # CSS merotasi elemen pada titik tengah box. Hasil rotate
                    # FFmpeg dapat lebih besar dari box, jadi pusat overlay
                    # harus dikunci kembali ke pusat koordinat editor.
                    f"[{current_video}][{media_label}]overlay="
                    f"x={x}+({element_width}-overlay_w)/2:"
                    f"y={y}+({element_height}-overlay_h)/2:"
                    f"eof_action=pass:shortest=0:format=auto[{next_video}]"
                )
                current_video = next_video
            elif media_type == "audio":
                next_video = f"v_audio_box_{media_cursor}"
                box_color = "#ff6161@0.22"
                filters.append(
                    f"[{current_video}]drawbox=x={x}:y={y}:w={element_width}:h={element_height}:"
                    f"color={box_color}:t=fill[{next_video}]"
                )
                current_video = next_video

            if probe.get("hasAudio") and not bool(element.get("muted")):
                source_duration = duration * speed
                volume = _clamp(_num(element.get("volume"), 100.0) / 100.0, 0.0, 2.0)
                audio_label = f"audio_{media_cursor}"
                filters.append(
                    f"[{input_index}:a]atrim=duration={source_duration:.6f},"
                    f"asetpts=PTS-STARTPTS,{_atempo_chain(speed)},"
                    f"volume={volume:.6f},apad,atrim=duration={duration:.6f}[{audio_label}]"
                )
                audio_labels.append(audio_label)

        if music_input is not None:
            music_volume = _clamp(_num(slide.get("musicVolume"), 100.0) / 100.0, 0.0, 2.0)
            filters.append(
                f"[{music_input}:a]atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,"
                f"volume={music_volume:.6f},apad,atrim=duration={duration:.6f}[music]"
            )
            audio_labels.append("music")

        filters.append(f"[{current_video}]format=yuv420p[vout]")
        audio_inputs = "".join(f"[{label}]" for label in audio_labels)
        filters.append(
            f"{audio_inputs}amix=inputs={len(audio_labels)}:duration=longest:normalize=0,"
            f"alimiter=limit=0.95,atrim=duration={duration:.6f}[aout]"
        )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        command.extend([
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-t",
            f"{duration:.6f}",
            *self._encoding_args(),
            str(output_path),
        ])
        run_command(command)

    def _extract_frame(self, segment: Path, destination: Path, *, last: bool) -> None:
        command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        if last:
            command.extend(["-sseof", "-0.05"])
        else:
            command.extend(["-ss", "0"])
        command.extend(["-i", str(segment), "-frames:v", "1", str(destination)])
        run_command(command)

    def render_standard_transition(
        self,
        *,
        source_segment: Path,
        target_segment: Path,
        source_slide: dict[str, Any],
        entry: PlaybackEntry,
        output_path: Path,
    ) -> None:
        duration = max(0.05, entry.duration)
        from_frame = self.work_dir / f"transition-{len(list(self.work_dir.glob('transition-*'))):04d}-from.png"
        to_frame = from_frame.with_name(from_frame.name.replace("-from.png", "-to.png"))
        self._extract_frame(source_segment, from_frame, last=True)
        self._extract_frame(target_segment, to_frame, last=False)

        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-t",
            f"{duration:.6f}",
            "-i",
            str(from_frame),
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-t",
            f"{duration:.6f}",
            "-i",
            str(to_frame),
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={duration:.6f}",
        ]
        music_input, _ = self._append_music_input(command, entry_start=entry.start)
        transition_type = str((entry.transition or {}).get("type") or "fade").lower()
        blend_expression = _transition_blend_expression(transition_type, duration)
        filters = [
            f"[0:v]scale={self.profile.width}:{self.profile.height},fps={FPS},format=yuv420p,setpts=PTS-STARTPTS[from]",
            f"[1:v]scale={self.profile.width}:{self.profile.height},fps={FPS},format=yuv420p,setpts=PTS-STARTPTS[to]",
            f"[from][to]blend=all_expr='{blend_expression}':shortest=1[vout]",
            f"[2:a]atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[silence]",
        ]
        audio_labels = ["silence"]
        if music_input is not None:
            volume = _clamp(_num(source_slide.get("musicVolume"), 100.0) / 100.0, 0.0, 2.0)
            filters.append(
                f"[{music_input}:a]atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,"
                f"volume={volume:.6f},apad,atrim=duration={duration:.6f}[music]"
            )
            audio_labels.append("music")
        audio_inputs = "".join(f"[{label}]" for label in audio_labels)
        filters.append(
            f"{audio_inputs}amix=inputs={len(audio_labels)}:duration=longest:normalize=0,"
            f"alimiter=limit=0.95,atrim=duration={duration:.6f}[aout]"
        )
        command.extend([
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-t",
            f"{duration:.6f}",
            *self._encoding_args(),
            str(output_path),
        ])
        run_command(command)

    def render(self, slides: list[dict[str, Any]], final_path: Path) -> float:
        sequence = build_playback_sequence(slides)
        if not sequence:
            raise RuntimeError("Timeline tidak memiliki slide biasa yang dapat di-Bake.")
        self._prepare_music_bed(max(entry.end for entry in sequence))

        normal_entries = [entry for entry in sequence if entry.kind == "slide"]
        normal_segments: dict[int, Path] = {}
        for index, entry in enumerate(normal_entries):
            path = self.work_dir / f"slide-{index:04d}-{entry.slide.get('id')}.mp4"
            logging.info("Render slide %s/%s: %s", index + 1, len(normal_entries), entry.slide.get("name"))
            self.render_slide(
                entry.slide,
                duration=entry.duration,
                entry_start=entry.start,
                output_path=path,
            )
            normal_segments[entry.source_slide_id] = path

        ordered_segments: list[Path] = []
        normal_index_by_id = {entry.source_slide_id: index for index, entry in enumerate(normal_entries)}
        for sequence_index, entry in enumerate(sequence):
            if entry.kind == "slide":
                ordered_segments.append(normal_segments[entry.source_slide_id])
                continue
            transition_path = self.work_dir / f"sequence-{sequence_index:04d}-{entry.kind}.mp4"
            if entry.kind == "custom-transition":
                logging.info("Render custom transition: %s", entry.slide.get("name"))
                self.render_slide(
                    entry.slide,
                    duration=entry.duration,
                    entry_start=entry.start,
                    output_path=transition_path,
                )
            else:
                source_path = normal_segments[entry.source_slide_id]
                source_index = normal_index_by_id[entry.source_slide_id]
                if source_index + 1 >= len(normal_entries):
                    continue
                target_path = normal_segments[normal_entries[source_index + 1].source_slide_id]
                logging.info("Render transition standar: %s", (entry.transition or {}).get("type"))
                self.render_standard_transition(
                    source_segment=source_path,
                    target_segment=target_path,
                    source_slide=entry.slide,
                    entry=entry,
                    output_path=transition_path,
                )
            ordered_segments.append(transition_path)

        concat_file = self.work_dir / "concat.txt"
        escaped_segments = [
            str(path.resolve()).replace("'", "'\\''")
            for path in ordered_segments
        ]
        concat_file.write_text(
            "\n".join(f"file '{path}'" for path in escaped_segments),
            encoding="utf-8",
        )
        stitched = self.work_dir / "stitched.mp4"
        run_command([
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(stitched),
        ])
        final_path.parent.mkdir(parents=True, exist_ok=True)
        run_command([
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(stitched),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(final_path),
        ])
        result = probe_media(final_path)
        if not result.get("hasVideo") or not result.get("hasAudio"):
            raise RuntimeError("Output FFmpeg tidak memiliki video dan audio stream lengkap.")
        duration = _num(result.get("duration"), 0.0)
        if duration <= 0:
            raise RuntimeError("Output FFmpeg memiliki durasi tidak valid.")
        return duration
