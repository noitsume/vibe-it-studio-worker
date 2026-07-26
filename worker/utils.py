from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("WORKER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def require_safe_id(value: str, label: str) -> str:
    if not SAFE_ID_RE.fullmatch(value):
        raise ValueError(
            f"{label} hanya boleh berisi huruf, angka, underscore, atau dash "
            "dengan panjang 1–128 karakter."
        )
    return value


def require_executable(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(f"Executable wajib tidak ditemukan: {name}")
    return executable


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    logging.debug("Menjalankan: %s", " ".join(command))
    return subprocess.run(
        list(command),
        cwd=str(cwd) if cwd else None,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def bounded_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    if value is None or not value.strip():
        return default
    parsed = int(value)
    return max(minimum, min(maximum, parsed))


def ensure_relative_b2_path(path: str, label: str = "B2 path") -> str:
    normalized = path.replace("\\", "/").strip("/")
    if not normalized or normalized.startswith(".") or ".." in normalized.split("/"):
        raise ValueError(f"{label} tidak valid: {path!r}")
    return normalized


