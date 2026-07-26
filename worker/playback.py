from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlaybackEntry:
    kind: str
    slide: dict[str, Any]
    source_slide_id: int
    transition: dict[str, Any] | None
    start: float
    end: float
    duration: float
    next_slide: dict[str, Any] | None = None


def _positive_duration(value: Any, minimum: float = 0.1) -> float:
    try:
        return max(minimum, float(value))
    except (TypeError, ValueError):
        return minimum


def build_playback_sequence(slides: list[dict[str, Any]]) -> list[PlaybackEntry]:
    bakeable = [slide for slide in slides if not bool(slide.get("isTransition"))]
    by_id = {int(slide.get("id", -1)): slide for slide in slides}
    result: list[PlaybackEntry] = []
    cursor = 0.0

    for index, slide in enumerate(bakeable):
        duration = _positive_duration(slide.get("duration"))
        source_id = int(slide.get("id", index))
        result.append(
            PlaybackEntry(
                kind="slide",
                slide=slide,
                source_slide_id=source_id,
                transition=None,
                start=cursor,
                end=cursor + duration,
                duration=duration,
            )
        )
        cursor += duration

        if index >= len(bakeable) - 1:
            continue

        transition = slide.get("transition") or {"type": "none", "duration": 0}
        transition_type = str(transition.get("type") or "none")
        try:
            transition_duration = max(0.0, float(transition.get("duration") or 0))
        except (TypeError, ValueError):
            transition_duration = 0.0
        if transition_type == "none" or transition_duration <= 0:
            continue

        next_slide = bakeable[index + 1]
        if transition_type.startswith("custom_slide_"):
            try:
                transition_id = int(transition_type.removeprefix("custom_slide_"))
            except ValueError:
                continue
            custom_slide = by_id.get(transition_id)
            if custom_slide and bool(custom_slide.get("isTransition")):
                result.append(
                    PlaybackEntry(
                        kind="custom-transition",
                        slide=custom_slide,
                        source_slide_id=source_id,
                        transition=transition,
                        start=cursor,
                        end=cursor + transition_duration,
                        duration=transition_duration,
                        next_slide=next_slide,
                    )
                )
                cursor += transition_duration
            continue

        result.append(
            PlaybackEntry(
                kind="standard-transition",
                slide=slide,
                source_slide_id=source_id,
                transition=transition,
                start=cursor,
                end=cursor + transition_duration,
                duration=transition_duration,
                next_slide=next_slide,
            )
        )
        cursor += transition_duration

    return result


def total_duration(slides: list[dict[str, Any]]) -> float:
    sequence = build_playback_sequence(slides)
    return sequence[-1].end if sequence else 0.0
