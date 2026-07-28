from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .utils import ensure_relative_b2_path


class B2Storage:
    def __init__(self, key_id: str, application_key: str, bucket_name: str) -> None:
        from b2sdk.v2 import B2Api, InMemoryAccountInfo

        info = InMemoryAccountInfo()
        self.api = B2Api(info)
        self.api.authorize_account("production", key_id, application_key)
        self.bucket = self.api.get_bucket_by_name(bucket_name)
        self.bucket_name = bucket_name

    def download(self, remote_path: str, local_path: Path) -> Path:
        remote = ensure_relative_b2_path(remote_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        logging.info("Download B2: %s", remote)
        downloaded = self.bucket.download_file_by_name(remote)
        downloaded.save_to(str(local_path))
        return local_path

    def upload(
        self,
        local_path: Path,
        remote_path: str,
        *,
        content_type: str = "b2/x-auto",
        file_info: dict[str, str] | None = None,
    ) -> Any:
        remote = ensure_relative_b2_path(remote_path)
        logging.info("Upload B2: %s", remote)
        return self.bucket.upload_local_file(
            local_file=str(local_path),
            file_name=remote,
            content_type=content_type,
            file_info=file_info or {},
        )

    def delete_prefix(self, prefix: str, *, apply: bool) -> int:
        safe_prefix = ensure_relative_b2_path(prefix)
        versions = [
            file_version
            for file_version, _folder in self.bucket.ls(
                folder_to_list=safe_prefix,
                latest_only=False,
                recursive=True,
            )
            if file_version.file_name.startswith(safe_prefix)
        ]
        for version in versions:
            logging.info("%s B2 %s", "DELETE" if apply else "DRY-RUN", version.file_name)
            if apply:
                self.api.delete_file_version(version.id_, version.file_name)
        return len(versions)

    def delete_prefix_except(
        self,
        prefix: str,
        *,
        keep_file_name: str,
        keep_version_id: str,
    ) -> int:
        safe_prefix = ensure_relative_b2_path(prefix)
        deleted = 0
        for version, _folder in self.bucket.ls(
            folder_to_list=safe_prefix,
            latest_only=False,
            recursive=True,
        ):
            if not version.file_name.startswith(safe_prefix):
                continue
            if version.file_name == keep_file_name and version.id_ == keep_version_id:
                continue
            self.api.delete_file_version(version.id_, version.file_name)
            deleted += 1
        return deleted
