from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .playback import build_playback_sequence

PROXY_MIN_BYTES = 32 * 1024 * 1024
PROXY_MIN_PIXEL_SAVING = 0.28
PROXY_SCALE_HEADROOM = 1.06
EXPENSIVE_DECODE_CODECS = {
    "av1",
    "hevc",
    "h265",
    "prores",
    "vp9",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _even(value: float, *, minimum: int = 2) -> int:
    rounded = max(minimum, math.ceil(value))
    return rounded if rounded % 2 == 0 else rounded + 1


@dataclass
class _Usage:
    occurrences: int = 0
    visible_seconds: float = 0.0
    max_box_width: int = 2
    max_box_height: int = 2


@dataclass(frozen=True)
class MediaOptimization:
    media_id: str
    action: str
    source_width: int
    source_height: int
    target_width: int
    target_height: int
    source_fps: float
    target_fps: float
    occurrences: int
    visible_seconds: float
    file_size: int
    reasons: tuple[str, ...]

    @property
    def uses_proxy(self) -> bool:
        return self.action == "proxy"


@dataclass(frozen=True)
class RenderPlan:
    peak_video_inputs: int
    total_video_occurrences: int
    optimizations: tuple[MediaOptimization, ...]

    @property
    def proxy_count(self) -> int:
        return sum(1 for item in self.optimizations if item.uses_proxy)

    def optimization_for(self, media_id: str) -> MediaOptimization | None:
        return next(
            (item for item in self.optimizations if item.media_id == media_id),
            None,
        )


def build_render_plan(
    *,
    slides: list[dict[str, Any]],
    output_width: int,
    output_height: int,
    media_by_id: dict[str, dict[str, Any]],
    media_paths: dict[str, Path],
    probes: dict[str, dict[str, Any]],
    output_fps: float,
) -> RenderPlan:
    """Build an immutable, deterministic optimization plan for one Bake.

    The planner never changes canvas geometry. It only decides whether a video
    should be decoded directly or through a high-quality, render-sized proxy.
    """

    usages: dict[str, _Usage] = {}
    peak_video_inputs = 0
    sequence = build_playback_sequence(slides)

    for entry in sequence:
        video_inputs = 0
        for element in list(entry.slide.get("elements") or []):
            if element.get("type") != "media":
                continue
            media_id = str(element.get("mediaId") or "")
            metadata = media_by_id.get(media_id) or {}
            media_type = str(element.get("mediaType") or metadata.get("type") or "")
            probe = probes.get(media_id) or {}
            if media_type != "video" or not probe.get("hasVideo"):
                continue

            video_inputs += 1
            usage = usages.setdefault(media_id, _Usage())
            usage.occurrences += 1
            usage.visible_seconds += max(0.1, float(entry.duration))
            usage.max_box_width = max(
                usage.max_box_width,
                _even(output_width * max(0.001, _number(element.get("w"), 10.0)) / 100),
            )
            usage.max_box_height = max(
                usage.max_box_height,
                _even(output_height * max(0.001, _number(element.get("h"), 10.0)) / 100),
            )

        peak_video_inputs = max(peak_video_inputs, video_inputs)

    optimizations: list[MediaOptimization] = []
    for media_id, usage in sorted(usages.items()):
        probe = probes.get(media_id) or {}
        source_width = max(2, int(_number(probe.get("width"), 2)))
        source_height = max(2, int(_number(probe.get("height"), 2)))
        source_fps = max(1.0, _number(probe.get("fps"), output_fps))
        source_pixels = source_width * source_height
        path = media_paths.get(media_id)
        try:
            file_size = path.stat().st_size if path else 0
        except OSError:
            file_size = 0

        # Preserve source aspect ratio while guaranteeing that every canvas box
        # can still use object-fit: cover without upscaling the proxy.
        cover_scale = max(
            usage.max_box_width / source_width,
            usage.max_box_height / source_height,
        )
        proxy_scale = min(1.0, cover_scale * PROXY_SCALE_HEADROOM)
        target_width = min(source_width, _even(source_width * proxy_scale))
        target_height = min(source_height, _even(source_height * proxy_scale))
        target_fps = min(source_fps, output_fps)
        target_pixels = target_width * target_height
        pixel_saving = max(0.0, 1.0 - (target_pixels / max(1, source_pixels)))
        codec = str(probe.get("codecName") or "").lower()

        reasons: list[str] = []
        if peak_video_inputs >= 3 and file_size >= PROXY_MIN_BYTES:
            reasons.append("multi-video-pressure")
        if usage.occurrences >= 2 and usage.visible_seconds >= 4:
            reasons.append("reused-source")
        if codec in EXPENSIVE_DECODE_CODECS:
            reasons.append(f"expensive-codec:{codec}")
        if source_fps > output_fps + 1:
            reasons.append("high-fps")

        meaningful_resize = pixel_saving >= PROXY_MIN_PIXEL_SAVING
        use_proxy = meaningful_resize and bool(reasons)
        optimizations.append(
            MediaOptimization(
                media_id=media_id,
                action="proxy" if use_proxy else "direct",
                source_width=source_width,
                source_height=source_height,
                target_width=target_width if use_proxy else source_width,
                target_height=target_height if use_proxy else source_height,
                source_fps=source_fps,
                target_fps=target_fps if use_proxy else source_fps,
                occurrences=usage.occurrences,
                visible_seconds=round(usage.visible_seconds, 3),
                file_size=file_size,
                reasons=tuple(reasons if use_proxy else ("direct-fidelity",)),
            )
        )

    return RenderPlan(
        peak_video_inputs=peak_video_inputs,
        total_video_occurrences=sum(item.occurrences for item in usages.values()),
        optimizations=tuple(optimizations),
    )
