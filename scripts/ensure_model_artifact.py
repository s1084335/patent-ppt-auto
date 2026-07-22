"""Download and verify the local PatentSBERTa artifact for deployment.

The production Docker image does not include model weights.  Lightning AI should
mount a persistent path as MODEL_ARTIFACT_ROOT, then run this script once before
starting the worker that needs embeddings.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
from urllib.request import urlopen
import zipfile


REQUIRED_MODEL_FILES = ("config.json", "modules.json", "tokenizer.json")
WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")


def default_target_dir() -> Path:
    """Resolve the local PatentSBERTa target directory from deployment env vars."""
    configured = os.getenv("PATENT_SBERTA_MODEL_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    root = Path(os.getenv("MODEL_ARTIFACT_ROOT", "data/model_artifacts")).expanduser()
    return root / "PatentSBERTa"


def model_is_ready(target_dir: Path) -> bool:
    """Return True when the target directory has the minimum files needed to load."""
    return (
        target_dir.is_dir()
        and all((target_dir / name).is_file() for name in REQUIRED_MODEL_FILES)
        and any((target_dir / name).is_file() for name in WEIGHT_FILES)
    )


def sha256_file(path: Path) -> str:
    """Calculate SHA-256 for a downloaded model archive."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    """Download the model archive from GitHub Releases or another HTTPS source."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url, timeout=120) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def safe_members_tar(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Reject archive entries that would escape the extraction directory."""
    safe: list[tarfile.TarInfo] = []
    for member in archive.getmembers():
        member_path = Path(member.name)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError(f"unsafe archive member: {member.name}")
        safe.append(member)
    return safe


def extract_archive(archive_path: Path, target_dir: Path) -> None:
    """Extract zip/tar model archive into the target directory."""
    with tempfile.TemporaryDirectory(prefix="patent-model-") as tmp_name:
        tmp_dir = Path(tmp_name)
        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path) as archive:
                for name in archive.namelist():
                    member_path = Path(name)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise ValueError(f"unsafe archive member: {name}")
                archive.extractall(tmp_dir)
        elif tarfile.is_tarfile(archive_path):
            with tarfile.open(archive_path) as archive:
                archive.extractall(tmp_dir, members=safe_members_tar(archive))
        else:
            raise ValueError(f"unsupported model archive format: {archive_path}")

        source_dir = normalize_extracted_root(tmp_dir)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, target_dir)


def normalize_extracted_root(tmp_dir: Path) -> Path:
    """Use the archive root, or its only child directory when the archive wraps files."""
    if model_is_ready(tmp_dir):
        return tmp_dir
    children = [path for path in tmp_dir.iterdir() if path.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir() and model_is_ready(children[0]):
        return children[0]
    raise FileNotFoundError(
        "extracted archive does not contain a ready PatentSBERTa model directory"
    )


def parse_args() -> argparse.Namespace:
    """Parse deployment-time model artifact options."""
    parser = argparse.ArgumentParser(description="Ensure PatentSBERTa is available locally.")
    parser.add_argument("--url", default=os.getenv("PATENT_SBERTA_MODEL_URL", ""))
    parser.add_argument("--sha256", default=os.getenv("PATENT_SBERTA_MODEL_SHA256", ""))
    parser.add_argument("--target-dir", type=Path, default=default_target_dir())
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    """Download the model archive when the local target is missing or forced."""
    args = parse_args()
    target_dir = args.target_dir.expanduser()
    if model_is_ready(target_dir) and not args.force:
        print(f"PatentSBERTa ready: {target_dir}")
        return
    if not args.url:
        raise SystemExit(
            "PatentSBERTa model is missing and PATENT_SBERTA_MODEL_URL is not set"
        )

    with tempfile.TemporaryDirectory(prefix="patent-model-download-") as tmp_name:
        archive_path = Path(tmp_name) / "PatentSBERTa.archive"
        print(f"Downloading PatentSBERTa from {args.url}")
        download(args.url, archive_path)
        if args.sha256:
            actual = sha256_file(archive_path)
            if actual.lower() != args.sha256.lower():
                raise SystemExit(
                    f"model archive sha256 mismatch: expected {args.sha256}, got {actual}"
                )
        extract_archive(archive_path, target_dir)
    if not model_is_ready(target_dir):
        raise SystemExit(f"PatentSBERTa model is not ready after extraction: {target_dir}")
    print(f"PatentSBERTa ready: {target_dir}")


if __name__ == "__main__":
    main()
