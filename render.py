#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from worker.ffmpeg_renderer import FFmpegRenderer, RESOLUTIONS
from worker.schema import validate_media_library, validate_timeline
from worker.utils import configure_logging, ensure_relative_b2_path, require_safe_id, sha256_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VibeItStudio FFmpeg render worker")
    parser.add_argument("--room_id", help="Firestore room ID")
    parser.add_argument("--job_id", help="Firestore render job ID")
    parser.add_argument("--resolution", choices=sorted(RESOLUTIONS), default="720")
    parser.add_argument(
        "--timeline-file",
        type=Path,
        help="Mode lokal: JSON timeline/snapshot tanpa Firebase atau B2.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output lokal. Default: output/final-<room/job>.mp4",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parent,
    )
    return parser.parse_args()


def _normalize_media_library(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        normalized.append(
            {
                **item,
                "id": str(item.get("id") or item.get("mediaId") or ""),
                "mediaId": str(item.get("mediaId") or item.get("id") or ""),
                "size": item.get("size", item.get("sizeBytes", 0)),
            }
        )
    return normalized


def _load_local_payload(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Path]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Timeline lokal harus berupa JSON object.")
    timeline = payload.get("timeline") if isinstance(payload.get("timeline"), dict) else payload
    media_library = _normalize_media_library(
        list(payload.get("mediaLibrary") or timeline.get("mediaLibrary") or [])
    )
    media_paths: dict[str, Path] = {}
    for media in media_library:
        local_path = media.get("localPath")
        if local_path:
            resolved = (path.parent / str(local_path)).resolve()
            if not resolved.exists():
                raise RuntimeError(f"Media lokal tidak ditemukan: {resolved}")
            media_paths[str(media["id"])] = resolved
    return timeline, media_library, media_paths


def _download_media(storage: Any, media_library: list[dict[str, Any]], media_dir: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for media in media_library:
        media_id = str(media.get("id") or media.get("mediaId") or "")
        remote_path = media.get("filePath")
        if not remote_path:
            logging.warning("Media %s tidak memiliki filePath; elemen terkait akan dilewati.", media_id)
            continue
        remote = ensure_relative_b2_path(str(remote_path), f"media {media_id}.filePath")
        suffix = Path(remote).suffix[:16] or ".bin"
        local = media_dir / f"{media_id}{suffix}"
        storage.download(remote, local)
        paths[media_id] = local
    return paths


def _render_local(args: argparse.Namespace) -> int:
    timeline, media_library, media_paths = _load_local_payload(args.timeline_file)
    validate_timeline(timeline, 950 * 1024)
    validate_media_library(media_library, 10 * 1024 * 1024 * 1024)
    output = args.output or Path("output/local-final.mp4")
    output = output.resolve()
    with tempfile.TemporaryDirectory(prefix="vibeit-local-") as temporary:
        renderer = FFmpegRenderer(
            project_root=args.project_root.resolve(),
            work_dir=Path(temporary),
            resolution=args.resolution,
            preset="medium",
            media_library=media_library,
            media_paths=media_paths,
            music=timeline.get("music") or {},
        )
        duration = renderer.render(list(timeline.get("slides") or []), output)
    logging.info("Render lokal selesai: %s (%.3f detik)", output, duration)
    return 0


def _render_remote(args: argparse.Namespace) -> int:
    if not args.room_id:
        raise RuntimeError("--room_id wajib untuk mode Firebase/B2.")
    room_id = require_safe_id(args.room_id, "room_id")
    requested_job_id = require_safe_id(args.job_id, "job_id") if args.job_id else None

    from worker.b2_storage import B2Storage
    from worker.config import load_worker_config
    from worker.firestore_store import FirestoreStore

    config = load_worker_config()
    store = FirestoreStore(config.service_account)
    storage = B2Storage(config.b2_key_id, config.b2_application_key, config.b2_bucket_name)
    job_id = require_safe_id(store.resolve_job_id(room_id, requested_job_id), "job_id")
    job_record_ready = False
    uploaded_version = None

    temporary_root = Path(tempfile.mkdtemp(prefix=f"vibeit-{room_id}-{job_id}-"))
    logging.info("Workspace: %s", temporary_root)
    try:
        job_snapshot = store.db.collection("render_jobs").document(job_id).get()
        job_preview = job_snapshot.to_dict() if job_snapshot.exists else {}
        job_record_ready = job_snapshot.exists
        snapshot_payload = None
        snapshot_path = (job_preview or {}).get("snapshotPath")
        if snapshot_path:
            snapshot_local = temporary_root / "snapshot.json"
            storage.download(str(snapshot_path), snapshot_local)
            snapshot_payload = store.load_snapshot_json(snapshot_local, config.max_timeline_bytes)

        source = store.load_source(
            room_id,
            job_id,
            snapshot=snapshot_payload,
            requested_resolution=args.resolution,
        )
        job_record_ready = True
        timeline = source.timeline
        media_library = _normalize_media_library(source.media_library)
        validate_timeline(timeline, config.max_timeline_bytes)
        validate_media_library(media_library, config.max_media_bytes)

        source_hash = timeline.get("contentHash") or source.job.get("sourceContentHash")
        store.mark_started(job_id, resolution=args.resolution, source_hash=source_hash)

        media_dir = temporary_root / "media"
        media_paths = _download_media(storage, media_library, media_dir)
        final_local = args.output.resolve() if args.output else temporary_root / "final.mp4"
        renderer = FFmpegRenderer(
            project_root=args.project_root.resolve(),
            work_dir=temporary_root / "render",
            resolution=args.resolution,
            preset=config.ffmpeg_preset,
            media_library=media_library,
            media_paths=media_paths,
            music=timeline.get("music") or {},
        )
        duration = renderer.render(list(timeline.get("slides") or []), final_local)
        final_remote = f"rooms/{room_id}/final/current.mp4"
        digest = sha256_file(final_local)
        uploaded_version = storage.upload(
            final_local,
            final_remote,
            content_type="video/mp4",
            file_info={
                "room-id": room_id,
                "job-id": job_id,
                "resolution": args.resolution,
                "content-sha256": digest,
            },
        )
        store.mark_success(
            room_id=room_id,
            job_id=job_id,
            resolution=args.resolution,
            final_path=final_remote,
            duration=duration,
            source_hash=source_hash,
            file_size=final_local.stat().st_size,
            sha256=digest,
            receiver_token_hash=source.room.get("receiverTokenHash"),
        )
        try:
            removed = storage.delete_prefix_except(
                f"rooms/{room_id}/final/",
                keep_file_name=final_remote,
                keep_version_id=uploaded_version.id_,
            )
            if removed:
                logging.info("Final B2 lama dibersihkan: %s object version(s).", removed)
        except Exception:
            # Final Firestore sudah menunjuk object baru; kegagalan housekeeping
            # tidak boleh membuat Bake sukses terlihat gagal.
            logging.exception("Gagal membersihkan final B2 lama.")
        logging.info(
            "Bake selesai: room=%s job=%s duration=%.3fs path=%s",
            room_id,
            job_id,
            duration,
            final_remote,
        )
        return 0
    except Exception as error:
        logging.exception("Bake gagal")
        if uploaded_version is not None:
            try:
                storage.api.delete_file_version(
                    uploaded_version.id_, uploaded_version.file_name
                )
                logging.info("Final B2 orphan dibersihkan setelah kegagalan Firestore.")
            except Exception:
                logging.exception("Gagal membersihkan final B2 orphan")
        if job_record_ready:
            try:
                store.mark_failed(job_id, error)
            except Exception:
                logging.exception("Gagal menulis status failed ke Firestore")
        raise
    finally:
        if config.keep_temp:
            logging.warning("WORKER_KEEP_TEMP aktif; workspace dipertahankan: %s", temporary_root)
        else:
            shutil.rmtree(temporary_root, ignore_errors=True)


def main() -> int:
    configure_logging()
    args = parse_args()
    if args.timeline_file:
        return _render_local(args)
    return _render_remote(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        logging.error("Render dibatalkan.")
        raise SystemExit(130)
    except Exception as error:
        logging.error("Worker berhenti: %s", error)
        raise SystemExit(1)
