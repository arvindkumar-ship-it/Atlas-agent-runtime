"""Package source, built console and checksums without local state or dependencies."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

IGNORED = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "test-results",
    "playwright-report",
}
LOCAL = {"data", "repositories", "build", "dist", "models"}


def source_files(root: Path):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or path.is_symlink():
            continue
        if set(relative.parts) & IGNORED or relative.parts[0] in LOCAL:
            continue
        if any(part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.name.startswith(".env") and path.name != ".env.example":
            continue
        if path.suffix in {".pyc", ".zip", ".db", ".db-shm", ".db-wal"}:
            continue
        if path.name != "RELEASE_MANIFEST.json":
            yield path


def package(root: Path, output: Path):
    files = list(source_files(root))
    entries = {
        path.relative_to(root).as_posix(): {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
        for path in files
    }
    manifest = root / "RELEASE_MANIFEST.json"
    manifest.write_text(
        json.dumps(
            {"format": 1, "project": "atlas-agent-runtime", "version": "1.0.0", "files": entries},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in [*files, manifest]:
            archive.write(path, "atlas-agent-runtime/" + path.relative_to(root).as_posix())
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None, "ZIP CRC check failed"
        assert len(archive.namelist()) == len(set(archive.namelist())), "Duplicate archive path"
        for name, entry in entries.items():
            assert (
                hashlib.sha256(archive.read("atlas-agent-runtime/" + name)).hexdigest()
                == entry["sha256"]
            )
    print(
        f"{output}: {len(files) + 1} files, {output.stat().st_size:,} bytes; CRC and SHA-256 verified"
    )


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=root.parent / "Atlas_Agent_Runtime.zip")
    args = parser.parse_args()
    package(root, args.output.resolve())


if __name__ == "__main__":
    main()
