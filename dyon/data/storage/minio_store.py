"""MinIO object store adapter."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dyon.core.config import TwinConfig

log = logging.getLogger(__name__)


class MinIOAdapter:
    """MinIO adapter for binary file / object storage."""

    def __init__(self, config: TwinConfig):
        from dyon._compat import require
        require("minio", "stores")
        from minio import Minio

        self._cfg = config.minio
        self._asset_id = config.asset_id
        self._client = Minio(
            self._cfg.endpoint,
            access_key=self._cfg.access_key,
            secret_key=self._cfg.secret_key,
            secure=self._cfg.secure,
        )
        self._bucket = self._cfg.bucket
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
        except Exception as e:
            log.warning("MinIO bucket setup error: %s", e)

    def _object_key(self, name: str) -> str:
        return f"{self._asset_id}/{name}"

    def upload_file(
        self, local_path: str, object_name: str | None = None
    ) -> str:
        name = object_name or os.path.basename(local_path)
        key = self._object_key(name)
        try:
            self._client.fput_object(self._bucket, key, local_path)
            return key
        except Exception as e:
            log.error("MinIO upload error: %s", e)
            raise

    def download_file(self, object_name: str, local_path: str) -> None:
        key = self._object_key(object_name)
        try:
            self._client.fget_object(self._bucket, key, local_path)
        except Exception as e:
            log.error("MinIO download error: %s", e)
            raise

    def list_files(self) -> list[str]:
        prefix = f"{self._asset_id}/"
        try:
            return [
                obj.object_name.removeprefix(prefix)
                for obj in self._client.list_objects(
                    self._bucket, prefix=prefix, recursive=True
                )
            ]
        except Exception as e:
            log.error("MinIO list_files error: %s", e)
            return []

    def delete_file(self, object_name: str) -> None:
        key = self._object_key(object_name)
        try:
            self._client.remove_object(self._bucket, key)
        except Exception as e:
            log.error("MinIO delete_file error: %s", e)
            raise

    def close(self) -> None:
        pass  # minio client has no explicit close
