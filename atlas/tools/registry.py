from __future__ import annotations

import ast
import asyncio
import difflib
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import Field

from atlas.config import Settings
from atlas.domain.models import StrictModel, ToolResult
from atlas.security.paths import iter_files, resolve_path, safe_environment
from atlas.tools.patch import apply_patch, parse_patch
from atlas.tools.process import run_process


class EmptyArgs(StrictModel):
    pass


class ListArgs(StrictModel):
    path: str = "."


class ReadArgs(StrictModel):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=250, ge=1)


class SearchArgs(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=60, ge=1, le=200)


class TestArgs(StrictModel):
    targets: list[str] = Field(default_factory=list, max_length=10)


class PatchArgs(StrictModel):
    patch: str = Field(min_length=1, max_length=128000)


@dataclass(frozen=True)
class Contract:
    name: str
    description: str
    schema: type[StrictModel]
    read_only: bool = True
    idempotent: bool = True
    timeout: int = 15
    approval: bool = False

    def export(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": self.schema.model_json_schema(),
            "read_only": self.read_only,
            "requires_approval": self.approval,
        }


CONTRACTS = {
    item.name: item
    for item in [
        Contract(
            "list_files",
            "List repository files; excludes secrets, symlinks and build folders",
            ListArgs,
        ),
        Contract(
            "read_file", "Read an exact file slice with line numbers (max 500 lines)", ReadArgs
        ),
        Contract(
            "search_text",
            "Literal case-insensitive text search with file and line references",
            SearchArgs,
        ),
        Contract(
            "run_tests",
            "Execute fixed pytest command; optional relative test file targets",
            TestArgs,
            read_only=False,
            timeout=60,
        ),
        Contract(
            "apply_patch",
            "Apply exact unified diff in isolated workspace; include --- a/path and +++ b/path headers",
            PatchArgs,
            read_only=False,
            approval=True,
        ),
        Contract(
            "git_diff",
            "Return changes from the immutable workspace baseline, including added files",
            EmptyArgs,
        ),
        Contract(
            "security_scan",
            "Run bounded AST checks for dangerous Python constructs; this is not a full security audit",
            EmptyArgs,
        ),
    ]
}


def file_text(path: Path) -> str:
    if path.stat().st_size > 128000:
        raise ValueError("File exceeds text tool limit")
    return path.read_text(encoding="utf-8")


def fingerprint(root: Path) -> str:
    value = hashlib.sha256()
    for path in iter_files(root):
        value.update(path.relative_to(root).as_posix().encode())
        value.update(b"\x00")
        value.update(path.read_bytes())
    return value.hexdigest()


class RepositoryTools:
    def __init__(
        self, root: Path, artifact_dir: Path, baseline: dict[str, str], settings: Settings
    ):
        self.root, self.artifacts, self.baseline, self.settings = (
            root,
            artifact_dir,
            baseline,
            settings,
        )
        self._effect_lock = asyncio.Lock()
        self._limit = asyncio.Semaphore(4)

    def validate(self, name: str, arguments: dict) -> StrictModel:
        if name not in CONTRACTS:
            raise PermissionError(f"Unknown tool: {name}")
        return CONTRACTS[name].schema.model_validate(arguments)

    async def execute(self, name: str, arguments: dict) -> ToolResult:
        args = self.validate(name, arguments)
        try:
            async with self._limit:
                if CONTRACTS[name].read_only:
                    return await self._dispatch(name, args)
                async with self._effect_lock:
                    return await self._dispatch(name, args)
        except (ValueError, OSError, PermissionError, UnicodeDecodeError) as exc:
            return ToolResult(ok=False, error=str(exc))

    async def _dispatch(self, name: str, args: Any) -> ToolResult:
        if name == "run_tests":
            return await self.run_tests(args)
        if name == "apply_patch":
            before_tests = {
                name: content for name, content in self.baseline.items() if is_test(name)
            }
            key = hashlib.sha256(args.patch.encode()).hexdigest()
            journal = self.artifacts / f"patch-{key}.json"
            changes = (
                json.loads(journal.read_text(encoding="utf-8"))["changes"]
                if journal.exists()
                else await asyncio.to_thread(parse_patch, self.root, args.patch)
            )
            if any(test_support(path) for path in changes):
                return ToolResult(
                    ok=False, error="Test runner and collection configuration cannot be patched"
                )
            if any(
                path in before_tests and after != before_tests[path]
                for path, (_, after) in changes.items()
            ):
                return ToolResult(
                    ok=False,
                    error="Existing tests must be preserved; add a separate regression test",
                )
            result = await asyncio.to_thread(apply_patch, self.root, self.artifacts, args.patch)
            return ToolResult(ok=True, output=json.dumps(result), data=result)
        return await asyncio.to_thread(self._inspect, name, args)

    def _inspect(self, name: str, args: Any) -> ToolResult:
        if name == "list_files":
            target = resolve_path(self.root, args.path, allow_root=True)
            if not target.is_dir():
                raise ValueError("Not a directory")
            paths = [path.relative_to(self.root).as_posix() for path in iter_files(target)]
            return ToolResult(ok=True, output="\n".join(paths), data={"files": paths})
        if name == "read_file":
            if args.end_line < args.start_line or args.end_line - args.start_line > 499:
                raise ValueError("Invalid line range")
            target = resolve_path(self.root, args.path)
            content = file_text(target)
            output = "\n".join(
                f"{index}: {line}"
                for index, line in enumerate(content.splitlines(), 1)
                if args.start_line <= index <= args.end_line
            )
            return ToolResult(
                ok=True,
                output=output,
                data={"path": args.path, "sha256": hashlib.sha256(content.encode()).hexdigest()},
            )
        if name == "search_text":
            results = []
            for path in iter_files(self.root):
                try:
                    content = file_text(path)
                except (ValueError, UnicodeDecodeError):
                    continue
                for number, line in enumerate(content.splitlines(), 1):
                    if args.query.casefold() in line.casefold():
                        results.append(f"{path.relative_to(self.root)}:{number}: {line[:500]}")
                        if len(results) >= args.limit:
                            return ToolResult(
                                ok=True, output="\n".join(results), data={"truncated": True}
                            )
            return ToolResult(ok=True, output="\n".join(results))
        if name == "git_diff":
            current = {}
            for path in iter_files(self.root):
                try:
                    current[path.relative_to(self.root).as_posix()] = file_text(path)
                except (ValueError, UnicodeDecodeError):
                    continue
            output, changed = [], []
            for path in sorted(self.baseline.keys() | current.keys()):
                old, new = self.baseline.get(path, ""), current.get(path, "")
                if old != new:
                    changed.append(path)
                    output.extend(
                        difflib.unified_diff(
                            old.splitlines(keepends=True),
                            new.splitlines(keepends=True),
                            fromfile=f"a/{path}",
                            tofile=f"b/{path}",
                        )
                    )
            return ToolResult(ok=True, output="".join(output), data={"changed_files": changed})
        if name == "security_scan":
            issues = []
            for path in iter_files(self.root):
                if path.suffix != ".py":
                    continue
                try:
                    tree = ast.parse(file_text(path))
                except (ValueError, SyntaxError) as exc:
                    issues.append(f"{path.relative_to(self.root)}: parse error: {exc}")
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    function = ast.unparse(node.func)
                    if function in {
                        "eval",
                        "exec",
                        "os.system",
                        "pickle.loads",
                        "pickle.load",
                    } or any(
                        keyword.arg == "shell"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True
                        for keyword in node.keywords
                    ):
                        issues.append(f"{path.relative_to(self.root)}:{node.lineno}: {function}")
            return ToolResult(
                ok=not issues,
                output="\n".join(issues) or "AST checks passed",
                data={"issues": issues},
                error="AST security checks failed" if issues else None,
            )
        raise PermissionError("Unknown tool")

    async def run_tests(self, args: TestArgs) -> ToolResult:
        for name in args.targets:
            if not name.endswith(".py") or not resolve_path(self.root, name).is_file():
                raise ValueError("Targets must be existing relative Python test files")
        targets = args.targets or [
            path.relative_to(self.root).as_posix()
            for path in iter_files(self.root)
            if is_test(path.name)
        ]
        flags = [
            "-q",
            "--disable-warnings",
            "-p",
            "no:cacheprovider",
            "-o",
            "addopts=",
            "-o",
            "pythonpath=.",
            "--",
            *targets,
        ]
        if self.settings.sandbox == "local":
            import pytest

            env = safe_environment()
            env["PYTHONPATH"] = str(Path(pytest.__file__).resolve().parent.parent)
            result = await run_process(
                [sys.executable, "-P", "-m", "pytest", *flags],
                self.root,
                self.settings.test_timeout,
                env=env,
            )
        else:
            if not shutil.which("docker"):
                return ToolResult(
                    ok=False,
                    error="Docker is required for repository execution; build atlas-sandbox:1.0 first",
                )
            if "," in str(self.root):
                raise ValueError("Docker workspace path cannot contain commas")
            name = "atlas-test-" + uuid4().hex
            command = [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--cpus=1",
                "--memory=512m",
                "--pids-limit=96",
                "--user=65534:65534",
                "--tmpfs=/tmp:rw,noexec,nosuid,size=128m",
                "--env=PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
                "--env=PYTHONDONTWRITEBYTECODE=1",
                "--mount",
                f"type=bind,source={self.root},target=/workspace,readonly",
                "-w",
                "/workspace",
                self.settings.sandbox_image,
                "python",
                "-P",
                "-m",
                "pytest",
                *flags,
            ]
            try:
                result = await run_process(command, self.root, self.settings.test_timeout)
            finally:
                await run_process(["docker", "rm", "-f", name], self.root, timeout=10)
        tests = sum(int(value) for value in re.findall(r"(\d+) passed", result.output))
        result.data.update(
            {
                "tests_passed": tests,
                "fingerprint": fingerprint(self.root),
                "sandbox": self.settings.sandbox,
            }
        )
        if result.ok and tests == 0:
            result.ok, result.error = False, "No passing tests were collected"
        return result


def is_test(path: str) -> bool:
    name = Path(path).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def test_support(path: str) -> bool:
    return (
        Path(path).name
        in {
            "conftest.py",
            "pytest.ini",
            ".pytest.ini",
            "pyproject.toml",
            "setup.cfg",
            "tox.ini",
            "pytest.py",
            "sitecustomize.py",
            "usercustomize.py",
        }
        or "pytest" in Path(path).parts
    )


async def verify(tools: RepositoryTools) -> dict:
    tests = await tools.execute("run_tests", {})
    security = await tools.execute("security_scan", {})
    diff = await tools.execute("git_diff", {})
    preserved = all(
        (tools.root / path).is_file() and file_text(tools.root / path) == content
        for path, content in tools.baseline.items()
        if is_test(path)
    )
    added_tests = [
        path
        for path in diff.data.get("changed_files", [])
        if is_test(path) and path not in tools.baseline
    ]
    changed = bool(diff.data.get("changed_files"))
    checks = [
        {"name": "tests", "passed": tests.ok, "output": tests.output, "error": tests.error},
        {
            "name": "python_ast_checks",
            "passed": security.ok,
            "output": security.output,
            "error": security.error,
        },
        {"name": "original_tests_preserved", "passed": preserved},
        {"name": "regression_test_added", "passed": bool(added_tests) if changed else True},
        {"name": "diff_available", "passed": diff.ok},
    ]
    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
        "diff": diff.output,
        "changed_files": diff.data.get("changed_files", []),
        "fingerprint": fingerprint(tools.root),
        "sandbox": tools.settings.sandbox,
        "tests_passed": tests.data.get("tests_passed", 0),
    }
