import difflib
import io
import sys
import zipfile

import pytest

from atlas.domain.models import new_id
from atlas.security.paths import iter_files, resolve_path, safe_environment
from atlas.security.uploads import extract_repository
from atlas.tools.patch import apply_patch, parse_patch
from atlas.tools.process import run_process
from atlas.tools.registry import RepositoryTools
from atlas.tools.workspace import WorkspaceManager


def diff(name, before, after):
    return "".join(
        difflib.unified_diff(
            before.splitlines(True),
            after.splitlines(True),
            fromfile="a/" + name,
            tofile="b/" + name,
        )
    )


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/etc/passwd",
        "a/../../b",
        "a\\b",
        ".git/config",
        ".github/workflows/run.yml",
        ".env",
        "secret.pem",
        ".ssh/key",
    ],
)
def test_path_guards(tmp_path, name):
    with pytest.raises((ValueError, PermissionError)):
        resolve_path(tmp_path, name)


def test_symlink_and_secret_copy_are_excluded(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "logic.py").write_text("answer = 42\n")
    (source / ".env").write_text("TOKEN=private")
    (source / "link.py").symlink_to(source / "logic.py")
    manager = WorkspaceManager(tmp_path / "data")
    run_id = new_id()
    root = manager.create(run_id, source)
    assert [path.name for path in iter_files(root)] == ["logic.py"]
    assert manager.create(run_id, source) == root
    assert manager.baseline(run_id) == {"logic.py": "answer = 42\n"}
    with pytest.raises(PermissionError):
        resolve_path(source, "link.py")


def test_environment_drops_credentials(monkeypatch):
    monkeypatch.setenv("ATLAS_MODEL_API_KEY", "private")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "private")
    environment = safe_environment()
    assert "ATLAS_MODEL_API_KEY" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files:
            archive.writestr(name, text)
    return buffer.getvalue()


@pytest.mark.parametrize("name", ["../outside.py", "/outside.py", "a/../../outside.py"])
def test_zip_traversal_is_rejected(tmp_path, name):
    with pytest.raises((ValueError, PermissionError)):
        extract_repository(zip_bytes([(name, "bad")]), tmp_path)
    assert not (tmp_path.parent / "outside.py").exists()


def test_zip_root_unwrapped_and_secrets_excluded(tmp_path):
    name = extract_repository(
        zip_bytes([("project-main/logic.py", "x = 1"), ("project-main/.env", "secret")]), tmp_path
    )
    assert (tmp_path / name / "logic.py").read_text() == "x = 1"
    assert not (tmp_path / name / ".env").exists()


def test_zip_symlink_rejected(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        archive.writestr(info, "../outside")
    with pytest.raises(ValueError, match="symbolic links"):
        extract_repository(buffer.getvalue(), tmp_path)


def test_patch_exact_match_idempotent_and_stale_recovery(tmp_path):
    root, artifacts = tmp_path / "repo", tmp_path / "artifacts"
    root.mkdir()
    target = root / "logic.py"
    target.write_text("x = 1\n")
    patch = diff("logic.py", "x = 1\n", "x = 2\n")
    first = apply_patch(root, artifacts, patch)
    assert target.read_text() == "x = 2\n"
    assert apply_patch(root, artifacts, patch) == first
    target.write_text("someone_else = 3\n")
    with pytest.raises(ValueError, match="recovery"):
        apply_patch(root, artifacts, patch)


def test_multifile_patch_recovers_after_write_failure(tmp_path, monkeypatch):
    import atlas.tools.patch as module

    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("x = 1\n")
    patch = diff("a.py", "x = 1\n", "x = 2\n") + diff("b.py", "x = 1\n", "x = 2\n")
    write = module.atomic_text

    def fail_second(path, text):
        if path.name == "b.py":
            raise OSError("simulated process interruption")
        write(path, text)

    monkeypatch.setattr(module, "atomic_text", fail_second)
    with pytest.raises(OSError):
        apply_patch(tmp_path, tmp_path / "journal", patch)
    assert (tmp_path / "a.py").read_text() == "x = 2\n"
    monkeypatch.setattr(module, "atomic_text", write)
    apply_patch(tmp_path, tmp_path / "journal", patch)
    assert (tmp_path / "b.py").read_text() == "x = 2\n"


def test_diff_content_can_resemble_headers(tmp_path):
    before, after = "-- old text\n", "++ new text\n"
    (tmp_path / "notes.txt").write_text(before)
    assert parse_patch(tmp_path, diff("notes.txt", before, after))["notes.txt"] == (before, after)


@pytest.mark.parametrize(
    "patch",
    [
        "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-wrong\n+new\n",
        "--- a/x\n+++ b/x\n@@ -1,2 +1 @@\n-old\n+new\n",
        "--- a/x\n+++ b/../escape\n@@ -1 +1 @@\n-old\n+new\n",
    ],
)
def test_bad_patch_does_not_write(tmp_path, patch):
    (tmp_path / "x").write_text("old\n")
    with pytest.raises((ValueError, PermissionError)):
        apply_patch(tmp_path, tmp_path / "journal", patch)
    assert (tmp_path / "x").read_text() == "old\n"


@pytest.mark.asyncio
async def test_original_test_cannot_be_modified(tmp_path, settings):
    baseline = {"test_logic.py": "def test_it():\n    assert False\n"}
    (tmp_path / "test_logic.py").write_text(baseline["test_logic.py"])
    tools = RepositoryTools(tmp_path, tmp_path / "artifacts", baseline, settings)
    result = await tools.execute(
        "apply_patch",
        {
            "patch": diff(
                "test_logic.py", baseline["test_logic.py"], "def test_it():\n    assert True\n"
            )
        },
    )
    assert not result.ok
    assert (tmp_path / "test_logic.py").read_text() == baseline["test_logic.py"]


@pytest.mark.asyncio
async def test_fixed_test_command_rejects_options(tmp_path, settings):
    tools = RepositoryTools(tmp_path, tmp_path / "artifacts", {}, settings)
    assert not (await tools.execute("run_tests", {"targets": ["--help"]})).ok


@pytest.mark.asyncio
async def test_process_timeout_and_exit_status(tmp_path):
    result = await run_process([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, 0.1)
    assert not result.ok and "timed out" in result.error.lower()
    result = await run_process(
        [sys.executable, "-c", "print('evidence'); raise SystemExit(7)"], tmp_path, 5
    )
    assert result.exit_code == 7 and not result.ok and "evidence" in result.output


@pytest.mark.asyncio
async def test_process_output_is_bounded(tmp_path):
    result = await run_process([sys.executable, "-c", "print('x' * 200000)"], tmp_path, 5)
    assert not result.ok and len(result.output) < 132000


def test_insert_at_end_and_full_delete(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("a\nb\n")
    changes = parse_patch(tmp_path, "--- a/notes.txt\n+++ b/notes.txt\n@@ -2,0 +3 @@\n+c\n")
    assert changes["notes.txt"][1] == "a\nb\nc\n"
    apply_patch(
        tmp_path,
        tmp_path / "artifacts",
        "--- a/notes.txt\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n",
    )
    assert not target.exists()


def test_partial_delete_rejected(tmp_path):
    (tmp_path / "notes.txt").write_text("a\nb\n")
    with pytest.raises(ValueError):
        parse_patch(tmp_path, "--- a/notes.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-a\n")


def test_invalid_zip_is_validation_error(tmp_path):
    with pytest.raises(ValueError, match="Invalid ZIP"):
        extract_repository(b"not a zip", tmp_path)
    assert not list(tmp_path.iterdir())


def test_zip_alias_duplicate_rejected(tmp_path):
    with pytest.raises(ValueError, match="Duplicate"):
        extract_repository(zip_bytes([("logic.py", "one"), ("./logic.py", "two")]), tmp_path)


@pytest.mark.asyncio
async def test_alias_path_cannot_bypass_original_test_guard(tmp_path, settings):
    before = "def test_it():\n    assert False\n"
    (tmp_path / "test_logic.py").write_text(before)
    tools = RepositoryTools(tmp_path, tmp_path / "artifacts", {"test_logic.py": before}, settings)
    result = await tools.execute(
        "apply_patch",
        {"patch": diff("./test_logic.py", before, "def test_it():\n    assert True\n")},
    )
    assert not result.ok
    assert (tmp_path / "test_logic.py").read_text() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["conftest.py", "pytest.py", "pytest.ini"])
async def test_model_cannot_add_test_runner_overrides(tmp_path, settings, name):
    tools = RepositoryTools(tmp_path, tmp_path / "artifacts", {}, settings)
    patch = f"--- /dev/null\n+++ b/{name}\n@@ -0,0 +1 @@\n+override = True\n"
    result = await tools.execute("apply_patch", {"patch": patch})
    assert not result.ok and not (tmp_path / name).exists()


@pytest.mark.asyncio
async def test_repository_module_cannot_shadow_installed_test_runner(tmp_path, settings):
    (tmp_path / "pytest.py").write_text("print('100 passed')\n")
    (tmp_path / "test_real.py").write_text("def test_failure():\n    assert False\n")
    result = await RepositoryTools(tmp_path, tmp_path / "artifacts", {}, settings).execute(
        "run_tests", {}
    )
    assert not result.ok and "1 failed" in result.output
