from __future__ import annotations

import json
import math
from typing import Any

from .utils import ensure_relative_b2_path

MAX_SLIDES = 200
MAX_ELEMENTS = 4000
MAX_SINGLE_SLIDE_SECONDS = 60 * 60
MAX_TOTAL_SECONDS = 6 * 60 * 60


def _finite_number(value: Any, label: str, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} harus berupa angka.") from error
    if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
        raise ValueError(f"{label} berada di luar batas {minimum}–{maximum}.")
    return parsed


def validate_timeline(timeline: dict[str, Any], max_bytes: int) -> None:
    encoded = json.dumps(timeline, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(
            f"Timeline {len(encoded)} byte melewati batas worker {max_bytes} byte. "
            "Simpan snapshot JSON besar di B2 dan kirim manifest yang sesuai."
        )

    slides = timeline.get("slides")
    if not isinstance(slides, list) or not slides:
        raise ValueError("timeline.slides wajib berupa array yang tidak kosong.")
    if len(slides) > MAX_SLIDES:
        raise ValueError(f"Jumlah slide melewati batas {MAX_SLIDES}.")

    element_count = 0
    total_duration = 0.0
    slide_ids: set[int] = set()
    for slide_index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            raise ValueError(f"Slide index {slide_index} bukan object.")
        try:
            slide_id = int(slide.get("id"))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Slide index {slide_index} memiliki id tidak valid.") from error
        if slide_id in slide_ids:
            raise ValueError(f"ID slide duplikat: {slide_id}")
        slide_ids.add(slide_id)
        duration = _finite_number(
            slide.get("duration", 0),
            f"slides[{slide_index}].duration",
            minimum=0,
            maximum=MAX_SINGLE_SLIDE_SECONDS,
        )
        if not slide.get("isTransition"):
            total_duration += max(0.1, duration)
        transition = slide.get("transition") or {}
        if transition.get("type") not in {None, "", "none"}:
            total_duration += _finite_number(
                transition.get("duration", 0),
                f"slides[{slide_index}].transition.duration",
                minimum=0,
                maximum=120,
            )
        elements = slide.get("elements") or []
        if not isinstance(elements, list):
            raise ValueError(f"slides[{slide_index}].elements bukan array.")
        element_count += len(elements)
        for element_index, element in enumerate(elements):
            if not isinstance(element, dict):
                raise ValueError(f"Element {slide_index}:{element_index} bukan object.")
            if element.get("type") not in {"text", "media"}:
                raise ValueError(f"Jenis element tidak didukung: {element.get('type')!r}")
            for key in ("x", "y", "w", "h", "rotation", "opacity", "zIndex"):
                _finite_number(
                    element.get(key, 0),
                    f"element {slide_index}:{element_index}.{key}",
                    minimum=-100000,
                    maximum=100000,
                )
            if element.get("type") == "media" and not str(element.get("mediaId") or ""):
                raise ValueError(f"Media element {slide_index}:{element_index} tidak memiliki mediaId.")

    if element_count > MAX_ELEMENTS:
        raise ValueError(f"Jumlah element melewati batas {MAX_ELEMENTS}.")
    if total_duration > MAX_TOTAL_SECONDS:
        raise ValueError("Durasi total timeline melewati batas enam jam.")


def validate_media_library(media_library: list[dict[str, Any]], max_total_bytes: int) -> None:
    ids: set[str] = set()
    total_bytes = 0
    for index, media in enumerate(media_library):
        if not isinstance(media, dict):
            raise ValueError(f"Media index {index} bukan object.")
        media_id = str(media.get("id") or media.get("mediaId") or "")
        if not media_id:
            raise ValueError(f"Media index {index} tidak memiliki id.")
        if media_id in ids:
            raise ValueError(f"Media ID duplikat: {media_id}")
        ids.add(media_id)
        if media.get("uploadStatus") not in {None, "ready", "mock", "local-template"}:
            raise ValueError(f"Media {media_id} belum siap di-Bake.")
        file_path = media.get("filePath")
        if file_path:
            ensure_relative_b2_path(str(file_path), f"media {media_id}.filePath")
        size = media.get("sizeBytes", media.get("size", 0)) or 0
        try:
            total_bytes += max(0, int(size))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Ukuran media {media_id} tidak valid.") from error
    if total_bytes > max_total_bytes:
        raise ValueError(
            f"Total media {total_bytes} byte melewati batas worker {max_total_bytes} byte."
        )
