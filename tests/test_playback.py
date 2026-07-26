from worker.playback import build_playback_sequence, total_duration


def test_playback_keeps_frontend_duration_contract() -> None:
    slides = [
        {
            "id": 1,
            "duration": 2,
            "isTransition": False,
            "transition": {"type": "fade", "duration": 0.5},
            "elements": [],
        },
        {
            "id": 2,
            "duration": 3,
            "isTransition": False,
            "transition": {"type": "none", "duration": 0},
            "elements": [],
        },
    ]
    sequence = build_playback_sequence(slides)
    assert [entry.kind for entry in sequence] == [
        "slide",
        "standard-transition",
        "slide",
    ]
    assert total_duration(slides) == 5.5


def test_custom_transition_uses_transition_slide() -> None:
    slides = [
        {
            "id": 1,
            "duration": 2,
            "isTransition": False,
            "transition": {"type": "custom_slide_99", "duration": 1.25},
            "elements": [],
        },
        {
            "id": 99,
            "duration": 5,
            "isTransition": True,
            "transition": {"type": "none", "duration": 0},
            "elements": [],
        },
        {
            "id": 2,
            "duration": 3,
            "isTransition": False,
            "transition": {"type": "none", "duration": 0},
            "elements": [],
        },
    ]
    sequence = build_playback_sequence(slides)
    assert sequence[1].kind == "custom-transition"
    assert sequence[1].slide["id"] == 99
    assert sequence[1].duration == 1.25
