from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RenderSource:
    room: dict[str, Any]
    timeline: dict[str, Any]
    media_library: list[dict[str, Any]]
    job: dict[str, Any]
    job_id: str


class FirestoreStore:
    def __init__(self, service_account: dict[str, Any]) -> None:
        import firebase_admin
        from firebase_admin import credentials, firestore

        try:
            firebase_admin.get_app()
        except ValueError:
            credential = credentials.Certificate(service_account)
            firebase_admin.initialize_app(
                credential,
                {"projectId": service_account["project_id"]},
            )
        self._firestore = firestore
        self.db = firestore.client()

    @property
    def server_timestamp(self) -> Any:
        return self._firestore.SERVER_TIMESTAMP

    def resolve_job_id(self, room_id: str, requested_job_id: str | None) -> str:
        if requested_job_id:
            return requested_job_id
        import os

        run_id = os.getenv("GITHUB_RUN_ID") or os.getenv("GITHUB_RUN_NUMBER") or "manual"
        return f"gh_{run_id}"

    def load_source(
        self,
        room_id: str,
        job_id: str,
        *,
        snapshot: dict[str, Any] | None = None,
        requested_resolution: str,
    ) -> RenderSource:
        room_ref = self.db.collection("rooms").document(room_id)
        timeline_ref = room_ref.collection("timeline").document("current")
        job_ref = self.db.collection("render_jobs").document(job_id)

        room_snapshot = room_ref.get()
        if not room_snapshot.exists:
            raise RuntimeError(f"Room tidak ditemukan: {room_id}")
        room = room_snapshot.to_dict() or {}

        job_snapshot = job_ref.get()
        if job_snapshot.exists:
            job = job_snapshot.to_dict() or {}
            if str(job.get("roomId")) != room_id:
                raise RuntimeError("render_jobs jobId tidak dimiliki room yang diminta.")
            if job.get("status") == "success":
                raise RuntimeError("Render job ini sudah berstatus success dan tidak boleh dijalankan ulang.")
        else:
            job = {
                "schemaVersion": 1,
                "roomId": room_id,
                "ownerUid": room.get("ownerUid"),
                "status": "queued",
                "resolution": requested_resolution,
                "attempt": 0,
                "createdAt": self.server_timestamp,
            }
            job_ref.set(job, merge=False)

        if snapshot is not None:
            timeline = {
                "schemaVersion": int(snapshot.get("schemaVersion", 2)),
                "roomId": room_id,
                "slides": snapshot.get("slides") or [],
                "music": snapshot.get("music") or {},
                "contentHash": snapshot.get("contentHash") or job.get("sourceContentHash"),
                "cloudRevision": snapshot.get("cloudRevision"),
            }
            media_library = list(snapshot.get("mediaLibrary") or [])
        else:
            timeline_snapshot = timeline_ref.get()
            if not timeline_snapshot.exists:
                raise RuntimeError(f"Timeline Firestore tidak ditemukan: rooms/{room_id}/timeline/current")
            timeline = timeline_snapshot.to_dict() or {}
            media_library = [
                {"id": doc.id, **(doc.to_dict() or {})}
                for doc in room_ref.collection("media").stream()
            ]

        source_hash = job.get("sourceContentHash")
        timeline_hash = timeline.get("contentHash")
        if source_hash and timeline_hash and source_hash != timeline_hash and snapshot is None:
            raise RuntimeError(
                "Timeline sudah berubah setelah render job dibuat. "
                "Buat snapshot immutable di B2 dan isi snapshotPath sebelum Bake."
            )

        return RenderSource(
            room=room,
            timeline=timeline,
            media_library=media_library,
            job=job,
            job_id=job_id,
        )

    def mark_started(self, job_id: str, *, resolution: str, source_hash: str | None) -> None:
        ref = self.db.collection("render_jobs").document(job_id)
        ref.set(
            {
                "status": "rendering",
                "resolution": resolution,
                "sourceContentHash": source_hash,
                "attempt": self._firestore.Increment(1),
                "startedAt": self.server_timestamp,
                "finishedAt": None,
                "errorCode": None,
                "errorMessage": None,
            },
            merge=True,
        )

    def mark_failed(self, job_id: str, error: BaseException) -> None:
        message = str(error).strip() or error.__class__.__name__
        self.db.collection("render_jobs").document(job_id).set(
            {
                "status": "failed",
                "finishedAt": self.server_timestamp,
                "errorCode": error.__class__.__name__[:80],
                "errorMessage": message[:1500],
            },
            merge=True,
        )

    def mark_success(
        self,
        *,
        room_id: str,
        job_id: str,
        resolution: str,
        final_path: str,
        duration: float,
        source_hash: str | None,
        file_size: int,
        sha256: str,
        receiver_token_hash: str | None,
    ) -> None:
        batch = self.db.batch()
        job_ref = self.db.collection("render_jobs").document(job_id)
        room_ref = self.db.collection("rooms").document(room_id)
        final_ref = room_ref.collection("final_media").document("current")

        batch.set(
            job_ref,
            {
                "status": "success",
                "finalPath": final_path,
                "finishedAt": self.server_timestamp,
                "errorCode": None,
                "errorMessage": None,
            },
            merge=True,
        )
        batch.set(
            final_ref,
            {
                "schemaVersion": 1,
                "roomId": room_id,
                "status": "ready",
                "filePath": final_path,
                "resolution": resolution,
                "duration": round(duration, 3),
                "sourceContentHash": source_hash,
                "sizeBytes": file_size,
                "sha256": sha256,
                "updatedAt": self.server_timestamp,
            },
            merge=False,
        )
        batch.set(
            room_ref,
            {
                "status": "Baked",
                "updatedAt": self.server_timestamp,
                "lastBakedAt": self.server_timestamp,
                "lastBakedContentHash": source_hash,
            },
            merge=True,
        )
        if receiver_token_hash:
            receiver_ref = self.db.collection("public_receivers").document(receiver_token_hash)
            batch.set(
                receiver_ref,
                {
                    "finalStatus": "ready",
                    "updatedAt": self.server_timestamp,
                },
                merge=True,
            )
        batch.commit()

    def load_snapshot_json(self, path: Path, max_bytes: int) -> dict[str, Any]:
        if path.stat().st_size > max_bytes:
            raise RuntimeError(
                f"Snapshot timeline {path.stat().st_size} byte melebihi batas worker {max_bytes} byte."
            )
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise RuntimeError("Snapshot timeline harus berupa JSON object.")
        return value
