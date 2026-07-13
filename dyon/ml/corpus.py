"""Training corpus manager — versioned datasets in MinIO."""

from __future__ import annotations

import contextlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dyon.data.storage.minio_store import MinIOAdapter

log = logging.getLogger(__name__)


class IntegrityError(RuntimeError):
    """Raised when a downloaded artifact does not match its manifest checksum."""


def _sha256_of(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class TrainingCorpus:
    """
    Organises training data in MinIO under:
        /{asset_id}/corpus/{dataset_name}/{version}/
    A manifest JSON at /{asset_id}/corpus/{dataset_name}/manifest.json
    tracks available versions and their metadata.
    """

    def __init__(self, minio: MinIOAdapter, asset_id: str) -> None:
        self._minio = minio
        self._asset_id = asset_id

    # Keys are relative to the asset namespace: MinIOAdapter prefixes
    # ``{asset_id}/`` itself, so including it here too would double it.
    def _manifest_key(self, dataset_name: str) -> str:
        return f"corpus/{dataset_name}/manifest.json"

    def _data_prefix(self, dataset_name: str, version: str) -> str:
        return f"corpus/{dataset_name}/{version}/"

    def _load_manifest(self, dataset_name: str) -> dict:
        import os
        import tempfile
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False
            ) as f:
                tmp_path = f.name
            self._minio.download_file(self._manifest_key(dataset_name), tmp_path)
            with open(tmp_path) as f:
                return json.load(f)
        except Exception:
            return {"dataset": dataset_name, "versions": []}
        finally:
            if tmp_path and os.path.exists(tmp_path):
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)

    def _save_manifest(self, dataset_name: str, manifest: dict) -> None:
        import os
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(manifest, f)
            tmp = f.name
        try:
            self._minio.upload_file(tmp, self._manifest_key(dataset_name))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def push_version(self, dataset_name: str, file_path: str,
                     metadata: dict[str, Any] | None = None) -> str:
        """Upload a dataset file and register a new version."""
        version = str(int(time.time()))
        object_key = f"{self._data_prefix(dataset_name, version)}{dataset_name}.data"
        digest = _sha256_of(file_path)
        self._minio.upload_file(file_path, object_key)

        manifest = self._load_manifest(dataset_name)
        manifest["versions"].append({
            "version": version,
            "object_key": object_key,
            "sha256": digest,
            "uploaded_at": time.time(),
            "metadata": metadata or {},
        })
        self._save_manifest(dataset_name, manifest)
        log.info("Corpus %s/%s pushed (version %s)", dataset_name, self._asset_id, version)
        return version

    def list_versions(self, dataset_name: str) -> list[dict]:
        return self._load_manifest(dataset_name).get("versions", [])

    def get_latest_version(self, dataset_name: str) -> str | None:
        versions = self.list_versions(dataset_name)
        if not versions:
            return None
        return versions[-1]["version"]

    def download_version(self, dataset_name: str, version: str,
                         dest_path: str) -> None:
        manifest = self._load_manifest(dataset_name)
        entry = next((v for v in manifest["versions"] if v["version"] == version), None)
        if entry is None:
            raise FileNotFoundError(f"Version {version} not found in {dataset_name}")
        self._minio.download_file(entry["object_key"], dest_path)

        # Verify the artifact is byte-identical to what the trusted trainer
        # uploaded before anything deserializes it (SB3/torch formats are pickle
        # and cannot be made safe by flags — integrity is the only control).
        expected = entry.get("sha256")
        if expected is not None:
            actual = _sha256_of(dest_path)
            if actual != expected:
                import os
                os.unlink(dest_path)
                raise IntegrityError(
                    f"Checksum mismatch for {dataset_name}@{version}: "
                    f"manifest says {expected[:12]}…, got {actual[:12]}…"
                )
        else:
            log.warning(
                "Corpus %s@%s has no checksum (pre-hardening upload); "
                "verification skipped", dataset_name, version,
            )
