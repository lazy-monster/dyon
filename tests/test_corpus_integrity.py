"""TrainingCorpus verifies artifacts against a manifest checksum before use.

Policies and reward nets are pickle-format files, so the control is: confirm the
downloaded bytes are identical to what the trusted trainer uploaded, before any
deserialization. A tampered artifact must raise rather than load.
"""

from __future__ import annotations

import pytest

from dyon.ml.corpus import IntegrityError, TrainingCorpus


class FakeMinIO:
    """In-memory object store: upload copies file bytes, download writes them back."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload_file(self, src_path: str, key: str) -> None:
        with open(src_path, "rb") as f:
            self.objects[key] = f.read()

    def download_file(self, key: str, dest_path: str) -> None:
        with open(dest_path, "wb") as f:
            f.write(self.objects[key])


def _write(path, data: bytes) -> str:
    path.write_bytes(data)
    return str(path)


def test_push_records_sha256_in_manifest(tmp_path):
    corpus = TrainingCorpus(FakeMinIO(), "asset_1")
    src = _write(tmp_path / "d.bin", b"payload-bytes" * 50)
    version = corpus.push_version("demos", src)
    entry = corpus.list_versions("demos")[-1]
    assert entry["version"] == version
    assert len(entry["sha256"]) == 64


def test_clean_download_succeeds(tmp_path):
    minio = FakeMinIO()
    corpus = TrainingCorpus(minio, "asset_1")
    src = _write(tmp_path / "d.bin", b"payload" * 100)
    version = corpus.push_version("demos", src)
    dest = tmp_path / "out.bin"
    corpus.download_version("demos", version, str(dest))
    assert dest.read_bytes() == b"payload" * 100


def test_tampered_download_raises_and_removes_file(tmp_path):
    minio = FakeMinIO()
    corpus = TrainingCorpus(minio, "asset_1")
    src = _write(tmp_path / "d.bin", b"trusted" * 100)
    version = corpus.push_version("demos", src)
    # Tamper with the stored object after upload.
    key = corpus.list_versions("demos")[-1]["object_key"]
    minio.objects[key] = b"evil-swapped-bytes"
    dest = tmp_path / "out.bin"
    with pytest.raises(IntegrityError):
        corpus.download_version("demos", version, str(dest))
    assert not dest.exists()   # poisoned file cleaned up


def test_legacy_entry_without_checksum_warns_but_loads(tmp_path, caplog):
    minio = FakeMinIO()
    corpus = TrainingCorpus(minio, "asset_1")
    src = _write(tmp_path / "d.bin", b"legacy" * 20)
    version = corpus.push_version("demos", src)
    # Simulate a pre-hardening manifest entry with no checksum.
    manifest = corpus._load_manifest("demos")
    manifest["versions"][-1].pop("sha256")
    corpus._save_manifest("demos", manifest)
    dest = tmp_path / "out.bin"
    with caplog.at_level("WARNING"):
        corpus.download_version("demos", version, str(dest))
    assert dest.read_bytes() == b"legacy" * 20
    assert any("no checksum" in r.message for r in caplog.records)
