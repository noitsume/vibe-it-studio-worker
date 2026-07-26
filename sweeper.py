#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from worker.b2_storage import B2Storage
from worker.config import load_worker_config
from worker.firestore_store import FirestoreStore
from worker.utils import bounded_int, configure_logging, parse_bool, require_safe_id


def _to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if hasattr(value, "to_datetime"):
        converted = value.to_datetime()
        return converted if converted.tzinfo else converted.replace(tzinfo=timezone.utc)
    return None


def _delete_subcollection_documents(store: FirestoreStore, room_id: str, names: list[str]) -> int:
    deleted = 0
    max_deletes = bounded_int(os.getenv("SWEEPER_MAX_METADATA_DELETES"), 200, 0, 450)
    if max_deletes <= 0:
        return 0
    room_ref = store.db.collection("rooms").document(room_id)
    batch = store.db.batch()
    for name in names:
        for document in room_ref.collection(name).limit(max_deletes - deleted).stream():
            batch.delete(document.reference)
            deleted += 1
            if deleted >= max_deletes:
                break
        if deleted >= max_deletes:
            break
    if deleted:
        batch.commit()
    return deleted


def main() -> int:
    configure_logging()
    apply_changes = parse_bool(os.getenv("SWEEPER_APPLY"), False)
    max_rooms = bounded_int(os.getenv("SWEEPER_MAX_ROOMS"), 20, 1, 100)
    delete_final = parse_bool(os.getenv("SWEEPER_DELETE_FINAL"), False)
    final_retention_days = bounded_int(
        os.getenv("SWEEPER_FINAL_RETENTION_DAYS"), 30, 1, 3650
    )
    prune_metadata = parse_bool(os.getenv("SWEEPER_PRUNE_FIRESTORE_METADATA"), False)

    config = load_worker_config()
    store = FirestoreStore(config.service_account)
    storage = B2Storage(config.b2_key_id, config.b2_application_key, config.b2_bucket_name)

    from google.cloud.firestore_v1.base_query import FieldFilter

    now = datetime.now(timezone.utc)
    query = (
        store.db.collection("rooms")
        .where(filter=FieldFilter("expiresAt", "<=", now))
        .limit(max_rooms)
    )
    rooms = list(query.stream())
    logging.info(
        "Sweeper mode=%s kandidat=%s max=%s",
        "APPLY" if apply_changes else "DRY-RUN",
        len(rooms),
        max_rooms,
    )

    affected = 0
    b2_versions = 0
    firestore_deletes = 0
    for snapshot in rooms:
        room_id = require_safe_id(snapshot.id, "room_id")
        room = snapshot.to_dict() or {}
        raw_deleted = bool(room.get("isRawFilesDeleted"))
        final_deleted = bool(room.get("isFinalVideoDeleted"))
        expires_at = _to_datetime(room.get("expiresAt"))

        raw_prefixes = [
            f"rooms/{room_id}/media/",
            f"rooms/{room_id}/submissions/",
            f"rooms/{room_id}/snapshots/",
        ]
        room_changed = False
        if not raw_deleted:
            for prefix in raw_prefixes:
                b2_versions += storage.delete_prefix(prefix, apply=apply_changes)
            room_changed = True

        should_delete_final = False
        if delete_final and not final_deleted and expires_at:
            should_delete_final = now >= expires_at + timedelta(days=final_retention_days)
            if should_delete_final:
                b2_versions += storage.delete_prefix(
                    f"rooms/{room_id}/final/", apply=apply_changes
                )
                room_changed = True

        if not room_changed:
            continue
        affected += 1
        logging.info(
            "%s room=%s raw=%s final=%s",
            "APPLY" if apply_changes else "DRY-RUN",
            room_id,
            not raw_deleted,
            should_delete_final,
        )
        if not apply_changes:
            continue

        update: dict[str, Any] = {"updatedAt": store.server_timestamp}
        if not raw_deleted:
            update.update({"isRawFilesDeleted": True, "rawMediaBytes": 0})
        if should_delete_final:
            update["isFinalVideoDeleted"] = True
        snapshot.reference.set(update, merge=True)

        if prune_metadata and not raw_deleted:
            firestore_deletes += _delete_subcollection_documents(
                store, room_id, ["media", "submissions"]
            )

    logging.info(
        "Selesai: rooms=%s B2 versions=%s Firestore metadata deletes=%s Firestore room writes=%s",
        affected,
        b2_versions,
        firestore_deletes,
        affected if apply_changes else 0,
    )
    if not apply_changes:
        logging.info("Tidak ada file atau dokumen yang dihapus. Set SWEEPER_APPLY=true untuk menerapkan.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        logging.error("Sweeper dibatalkan.")
        raise SystemExit(130)
    except Exception as error:
        logging.exception("Sweeper gagal: %s", error)
        raise SystemExit(1)
