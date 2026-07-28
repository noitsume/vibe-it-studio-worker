from pathlib import Path

from worker.render_planner import PROXY_MIN_BYTES, build_render_plan


def _video_element(media_id: str, *, x: float, y: float) -> dict:
    return {
        "id": f"element-{media_id}",
        "type": "media",
        "mediaType": "video",
        "mediaId": media_id,
        "x": x,
        "y": y,
        "w": 50,
        "h": 50,
        "rotation": 0,
        "opacity": 1,
        "zIndex": 0,
    }


def _large_sparse_file(path: Path) -> Path:
    with path.open("wb") as handle:
        handle.truncate(PROXY_MIN_BYTES + 1)
    return path


def test_four_large_videos_use_render_sized_proxies(tmp_path: Path) -> None:
    media_ids = [f"video-{index}" for index in range(4)]
    media_paths = {
        media_id: _large_sparse_file(tmp_path / f"{media_id}.mp4")
        for media_id in media_ids
    }
    probes = {
        media_id: {
            "hasVideo": True,
            "hasAudio": True,
            "codecName": "h264",
            "width": 3840,
            "height": 2160,
            "fps": 60,
        }
        for media_id in media_ids
    }
    slides = [{
        "id": 1,
        "duration": 30,
        "transition": {"type": "none", "duration": 0},
        "elements": [
            _video_element(media_ids[0], x=0, y=0),
            _video_element(media_ids[1], x=50, y=0),
            _video_element(media_ids[2], x=0, y=50),
            _video_element(media_ids[3], x=50, y=50),
        ],
    }]

    plan = build_render_plan(
        slides=slides,
        output_width=1920,
        output_height=1080,
        media_by_id={
            media_id: {"id": media_id, "type": "video"}
            for media_id in media_ids
        },
        media_paths=media_paths,
        probes=probes,
        output_fps=30,
    )

    assert plan.peak_video_inputs == 4
    assert plan.proxy_count == 4
    assert all(item.target_width < item.source_width for item in plan.optimizations)
    assert all(item.target_fps == 30 for item in plan.optimizations)
    assert all("multi-video-pressure" in item.reasons for item in plan.optimizations)


def test_video_near_output_size_stays_direct(tmp_path: Path) -> None:
    media_id = "video-direct"
    path = _large_sparse_file(tmp_path / "direct.mp4")
    slide = {
        "id": 1,
        "duration": 30,
        "transition": {"type": "none", "duration": 0},
        "elements": [{
            **_video_element(media_id, x=0, y=0),
            "w": 100,
            "h": 100,
        }],
    }

    plan = build_render_plan(
        slides=[slide],
        output_width=1280,
        output_height=720,
        media_by_id={media_id: {"id": media_id, "type": "video"}},
        media_paths={media_id: path},
        probes={
            media_id: {
                "hasVideo": True,
                "hasAudio": True,
                "codecName": "h264",
                "width": 1280,
                "height": 720,
                "fps": 30,
            }
        },
        output_fps=30,
    )

    optimization = plan.optimization_for(media_id)
    assert optimization is not None
    assert optimization.action == "direct"
    assert optimization.target_width == 1280
    assert optimization.target_height == 720
