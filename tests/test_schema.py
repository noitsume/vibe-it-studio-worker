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


def test_timeline_accepts_instance_effect_settings() -> None:
    timeline = valid_timeline()
    timeline["slides"][0]["elements"] = [
        {
            "id": "media-1",
            "type": "media",
            "mediaType": "video",
            "mediaId": "m1",
            "x": 5,
            "y": 8,
            "w": 80,
            "h": 70,
            "rotation": 12,
            "opacity": 0.8,
            "zIndex": 1,
            "fit": "contain",
            "focalX": 35,
            "focalY": 70,
            "brightness": 105,
            "contrast": 110,
            "saturation": 90,
            "grayscale": 10,
            "blur": 1.5,
            "fadeIn": 0.5,
            "fadeOut": 0.5,
            "audioPan": -25,
            "bassGain": 2,
            "midGain": 0,
            "trebleGain": -1,
        }
    ]
    validate_timeline(timeline, 900 * 1024)


def test_timeline_rejects_unknown_media_fit() -> None:
    timeline = valid_timeline()
    timeline["slides"][0]["elements"] = [
        {
            "id": "media-1",
            "type": "media",
            "mediaId": "m1",
            "x": 0,
            "y": 0,
            "w": 10,
            "h": 10,
            "rotation": 0,
            "opacity": 1,
            "zIndex": 0,
            "fit": "stretch-forever",
        }
    ]
    with pytest.raises(ValueError, match="fit"):
        validate_timeline(timeline, 900 * 1024)
