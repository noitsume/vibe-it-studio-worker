from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from .utils import bounded_int, parse_bool


@dataclass(frozen=True)
class WorkerConfig:
    service_account: dict[str, Any]
    b2_key_id: str
    b2_application_key: str
    b2_bucket_name: str
    max_media_bytes: int
    max_timeline_bytes: int
    ffmpeg_preset: str
    download_workers: int
    proxy_workers: int
    keep_temp: bool


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Environment secret belum tersedia: {name}")
    return value.strip()


def load_service_account() -> dict[str, Any]:
    raw = _required_env("FIREBASE_SERVICE_ACCOUNT")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "FIREBASE_SERVICE_ACCOUNT harus berisi seluruh JSON service account, bukan path file."
        ) from error

    required = {"project_id", "private_key", "client_email", "type"}
    missing = sorted(required.difference(parsed))
    if missing:
        raise RuntimeError(
            "FIREBASE_SERVICE_ACCOUNT tidak lengkap. Field hilang: " + ", ".join(missing)
        )
    if parsed.get("type") != "service_account":
        raise RuntimeError("FIREBASE_SERVICE_ACCOUNT bukan credential service_account.")
    return parsed


def load_worker_config() -> WorkerConfig:
    preset = os.getenv("WORKER_FFMPEG_PRESET", "medium").strip()
    allowed_presets = {
        "ultrafast",
        "superfast",
        "veryfast",
        "faster",
        "fast",
        "medium",
        "slow",
    }
    if preset not in allowed_presets:
        raise RuntimeError(f"WORKER_FFMPEG_PRESET tidak valid: {preset}")

    return WorkerConfig(
        service_account=load_service_account(),
        b2_key_id=_required_env("B2_KEY_ID"),
        b2_application_key=_required_env("B2_APPLICATION_KEY"),
        b2_bucket_name=_required_env("B2_BUCKET_NAME"),
        max_media_bytes=bounded_int(
            os.getenv("WORKER_MAX_MEDIA_BYTES"),
            2 * 1024 * 1024 * 1024,
            10 * 1024 * 1024,
            10 * 1024 * 1024 * 1024,
        ),
        max_timeline_bytes=bounded_int(
            os.getenv("WORKER_MAX_TIMELINE_BYTES"),
            900 * 1024,
            64 * 1024,
            950 * 1024,
        ),
        ffmpeg_preset=preset,
        download_workers=bounded_int(
            os.getenv("WORKER_DOWNLOAD_WORKERS"),
            4,
            1,
            8,
        ),
        proxy_workers=bounded_int(
            os.getenv("WORKER_PROXY_WORKERS"),
            2,
            1,
            4,
        ),
        keep_temp=parse_bool(os.getenv("WORKER_KEEP_TEMP"), False),
    )
