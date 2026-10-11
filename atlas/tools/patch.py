from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from atlas.security.paths import resolve_path

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_patch(root: Path, patch: str) -> dict[str, tuple[str | None, str | None]]:
    """Apply exact unified hunks. No fuzzy matching, renames, binary files or mode changes."""
    if len(patch.encode()) > 128000 or any(
        marker in patch
        for marker in ("GIT binary patch", "new mode ", "old mode ", "120000", "rename from ")
    ):
        raise ValueError("Unsupported patch type or size")
    lines = patch.splitlines(keepends=True)
    result, index = {}, 0
    while index < len(lines):
        if lines[index].startswith("diff --git ") or lines[index].startswith("index "):
            index += 1
            continue
        if not lines[index].startswith("--- "):
            raise ValueError("Expected unified diff header")
        old_name = lines[index][4:].strip()
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise ValueError("Missing new-file header")
        new_name = lines[index][4:].strip()
        index += 1
        old_name = old_name.removeprefix("a/")
        new_name = new_name.removeprefix("b/")
        if old_name != "/dev/null" and new_name != "/dev/null" and old_name != new_name:
            raise ValueError("Renames are not supported")
        name = old_name if new_name == "/dev/null" else new_name
        target = resolve_path(root, name)
        name = target.relative_to(root.resolve()).as_posix()
        if name in result:
            raise ValueError("Duplicate patch target")
        before = target.read_text(encoding="utf-8") if target.exists() else None
        if old_name == "/dev/null" and before is not None:
            raise ValueError("New file already exists")
        if old_name != "/dev/null" and before is None:
            raise ValueError("Patch target does not exist")
        original, output, cursor, hunks = (before or "").splitlines(keepends=True), [], 0, 0
        while index < len(lines) and lines[index].startswith("@@ "):
            match = HUNK.match(lines[index])
            if not match:
                raise ValueError("Invalid hunk header")
            old_start, old_count, new_start, new_count = match.groups()
            old_length, new_length = int(old_count or 1), int(new_count or 1)
            if (int(old_start) == 0 and old_length) or (int(new_start) == 0 and new_length):
                raise ValueError("Nonempty hunks need one-based positions")
            start = int(old_start) - 1 if old_length else int(old_start)
            if start < cursor or start > len(original):
                raise ValueError("Overlapping or invalid hunk")
            output.extend(original[cursor:start])
            new_position = int(new_start) - 1 if new_length else int(new_start)
            if new_position != len(output):
                raise ValueError("New hunk position does not match output")
            cursor, consumed, produced = start, 0, 0
            index += 1
            while index < len(lines) and (
                consumed < int(old_count or 1) or produced < int(new_count or 1)
            ):
                line = lines[index]
                prefix, content = line[0], line[1:]
                if prefix not in {" ", "+", "-"}:
                    raise ValueError("Invalid hunk line")
                index += 1
                if index < len(lines) and lines[index].startswith("\\ No newline at end of file"):
                    content = content.removesuffix("\n")
                    index += 1
                if prefix in {" ", "-"}:
                    if cursor >= len(original) or original[cursor] != content:
                        raise ValueError("Patch does not match current content")
                    cursor += 1
                    consumed += 1
                if prefix in {" ", "+"}:
                    output.append(content)
                    produced += 1
            if consumed != int(old_count or 1) or produced != int(new_count or 1):
                raise ValueError("Hunk line count mismatch")
            hunks += 1
        if not hunks:
            raise ValueError("Patch has no hunks")
        output.extend(original[cursor:])
        if new_name == "/dev/null" and output:
            raise ValueError("Deletion must remove the entire file")
        after = None if new_name == "/dev/null" else "".join(output)
        result[name] = (before, after)
    if not result:
        raise ValueError("Empty patch")
    return result


def atomic_text(target: Path, content: str):
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=target.parent, prefix="atlas-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def apply_patch(root: Path, artifact_dir: Path, patch: str) -> dict:
    key = hashlib.sha256(patch.encode()).hexdigest()
    journal = artifact_dir / f"patch-{key}.json"
    if journal.exists():
        changes = json.loads(journal.read_text(encoding="utf-8"))["changes"]
    else:
        changes = parse_patch(root, patch)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        atomic_text(journal, json.dumps({"changes": changes}))
    # A crash can occur between any two writes. Recover only known before/after versions.
    for name, (before, after) in changes.items():
        target = resolve_path(root, name)
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current not in (before, after):
            raise ValueError("Workspace changed during patch recovery")
    for name, (_, after) in changes.items():
        target = resolve_path(root, name)
        if after is None:
            target.unlink(missing_ok=True)
        else:
            atomic_text(target, after)
    return {"changed_files": sorted(changes), "patch_hash": key}
