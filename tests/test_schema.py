import pytest

from worker.schema import validate_media_library, validate_timeline


def valid_timeline() -> dict:
    return {
        "slides": [
            {
                "id": 1,
                "duration": 1,
                "isTransition": False,
                "transition": {"type": "none", "duration": 0},
                "elements": [],
            }
        ],
        "music": {"mediaId": None},
    }


def test_timeline_accepts_small_valid_payload() -> None:
    validate_timeline(valid_timeline(), 900 * 1024)


def test_timeline_rejects_duplicate_slide_id() -> None:
    timeline = valid_timeline()
    timeline["slides"].append(dict(timeline["slides"][0]))
    with pytest.raises(ValueError, match="duplikat"):
        validate_timeline(timeline, 900 * 1024)


def test_media_rejects_parent_path() -> None:
    with pytest.raises(ValueError):
        validate_media_library(
            [
                {
                    "id": "m1",
                    "filePath": "../secret.json",
                    "sizeBytes": 1,
                    "uploadStatus": "ready",
                }
            ],
            1000,
        )
