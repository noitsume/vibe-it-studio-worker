from __future__ import annotations

import argparse

from worker.config import load_service_account
from worker.firestore_store import FirestoreStore
from worker.utils import require_safe_id


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tandai render job gagal jika pipeline berhenti sebelum renderer."
    )
    parser.add_argument("--job_id", required=True)
    parser.add_argument("--message", required=True)
    args = parser.parse_args()

    job_id = require_safe_id(args.job_id, "job_id")
    store = FirestoreStore(load_service_account())
    snapshot = store.db.collection("render_jobs").document(job_id).get()
    if not snapshot.exists:
        return 0

    status = (snapshot.to_dict() or {}).get("status")
    if status in {"success", "failed"}:
        return 0

    store.mark_failed(job_id, RuntimeError(args.message))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
