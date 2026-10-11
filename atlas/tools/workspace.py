from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from atlas.security.paths import iter_files


class WorkspaceManager:
    def __init__(self, data_dir: Path):
        self.root = (data_dir / "workspaces").resolve()
        self.artifacts = (data_dir / "artifacts").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)

    def create(self, run_id: str, source: Path) -> Path:
        uuid.UUID(run_id)
        destination = self.root / run_id
        artifact = self.artifacts / run_id
        artifact.mkdir(parents=True, exist_ok=True)
        if destination.exists() and (artifact / "baseline.json").is_file():
            return destination
        staging = self.root / f"{run_id}.building"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        baseline, total = {}, 0
        try:
            for path in iter_files(source):
                if path.stat().st_size > 2 * 1024**2:
                    raise ValueError(f"File exceeds 2 MiB: {path.relative_to(source)}")
                content = path.read_bytes()
                total += len(content)
                if total > 32 * 1024**2:
                    raise ValueError("Repository exceeds 32 MiB")
                relative = path.relative_to(source).as_posix()
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                try:
                    baseline[relative] = content.decode("utf-8")
                except UnicodeDecodeError:
                    continue
            temporary = artifact / "baseline.tmp"
            temporary.write_text(json.dumps(baseline), encoding="utf-8")
            temporary.replace(artifact / "baseline.json")
            if destination.exists():
                shutil.rmtree(destination)
            staging.replace(destination)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return destination

    def baseline(self, run_id: str) -> dict[str, str]:
        return json.loads((self.artifacts / run_id / "baseline.json").read_text(encoding="utf-8"))
