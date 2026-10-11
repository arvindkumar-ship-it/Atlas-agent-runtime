from __future__ import annotations

import io
import shutil
import stat
import zipfile
from pathlib import Path
from uuid import uuid4

from atlas.security.paths import EXCLUDED, resolve_path, sensitive


def extract_repository(payload: bytes, repo_root: Path) -> str:
    if len(payload) > 8 * 1024**2:
        raise ValueError("Archive exceeds 8 MiB")
    name = "upload-" + uuid4().hex[:12]
    destination = repo_root / name
    destination.mkdir(parents=True)
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            items = archive.infolist()
            if len(items) > 5000 or sum(item.file_size for item in items) > 32 * 1024**2:
                raise ValueError("Archive exceeds extraction limits")
            written = set()
            for item in items:
                if item.flag_bits & 1:
                    raise ValueError("Encrypted archive members are not supported")
                path = Path(item.filename)
                if any(part in EXCLUDED for part in path.parts) or sensitive(path):
                    continue
                target = resolve_path(destination, item.filename.rstrip("/"), allow_root=True)
                normalized = target.relative_to(destination).as_posix()
                if normalized in written or stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError("Duplicate entries and symbolic links are forbidden")
                written.add(normalized)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if item.file_size > 2 * 1024**2 or (
                    item.compress_size and item.file_size / item.compress_size > 200
                ):
                    raise ValueError("Archive member exceeds file or compression limits")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("wb") as stream:
                    shutil.copyfileobj(source, stream, length=65536)
        # Common GitHub ZIPs wrap files in one top-level folder.
        children = list(destination.iterdir())
        return str(
            (
                children[0] if len(children) == 1 and children[0].is_dir() else destination
            ).relative_to(repo_root)
        )
    except zipfile.BadZipFile as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise ValueError("Invalid ZIP archive") from exc
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
