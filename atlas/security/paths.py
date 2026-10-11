from __future__ import annotations

import os
from pathlib import Path

EXCLUDED = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
}
PROTECTED = {".git", ".github", ".atlas", ".ssh", ".aws", ".npmrc", ".pypirc"}


def sensitive(path: Path) -> bool:
    parts = [part.casefold() for part in path.parts]
    return any(part in PROTECTED for part in parts) or any(
        part == ".env" or part.startswith(".env.") or part.endswith((".pem", ".key", ".p12"))
        for part in parts
    )


def resolve_path(root: Path, relative: str, *, allow_root: bool = False) -> Path:
    root = root.resolve()
    path = Path(relative)
    if (
        path.is_absolute()
        or "\\" in relative
        or "\x00" in relative
        or ":" in relative
        or ".." in path.parts
        or sensitive(path)
    ):
        raise PermissionError("Path is outside the repository or protected")
    target = root / path
    for parent in [target, *target.parents]:
        if parent == root:
            break
        if parent.is_symlink():
            raise PermissionError("Symbolic links are not allowed")
    if not target.resolve().is_relative_to(root) or (target == root and not allow_root):
        raise PermissionError("Path escapes repository")
    return target


def iter_files(root: Path, limit: int = 5000):
    count = 0
    for directory, dirs, filenames in os.walk(root, followlinks=False):
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in EXCLUDED
            and not sensitive(Path(name))
            and not (Path(directory) / name).is_symlink()
        )
        for name in sorted(filenames):
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file() or sensitive(path.relative_to(root)):
                continue
            count += 1
            if count > limit:
                raise ValueError("Repository file limit exceeded")
            yield path


def safe_environment() -> dict[str, str]:
    result = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMP", "TEMP")
        if key in os.environ
    }
    result.update(
        {
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return result
