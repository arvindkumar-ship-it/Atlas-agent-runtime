from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Demo:
    id: str
    title: str
    instruction: str
    source: str
    fixed: str
    tests: str
    regression: str


DEMOS = {
    demo.id: demo
    for demo in [
        Demo(
            "auth",
            "Token expiry",
            "Fix the authentication expiry unit mismatch without changing signature validation. Add a regression test and verify the patch.",
            "import hashlib\nimport hmac\n\n\ndef validate_token(expiry, now, signature, secret):\n    payload = str(expiry).encode()\n    expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()\n    if not hmac.compare_digest(signature, expected):\n        return False\n    return expiry > now * 1000\n",
            "import hashlib\nimport hmac\n\n\ndef validate_token(expiry, now, signature, secret):\n    payload = str(expiry).encode()\n    expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()\n    if not hmac.compare_digest(signature, expected):\n        return False\n    return expiry > now\n",
            'import hashlib\nimport hmac\nfrom logic import validate_token\n\n\ndef sign(expiry):\n    return hmac.new(b"test-only", str(expiry).encode(), hashlib.sha256).hexdigest()\n\n\ndef test_valid_token():\n    assert validate_token(1700000060, 1700000000, sign(1700000060), b"test-only")\n\n\ndef test_expired_token():\n    assert not validate_token(1699999999, 1700000000, sign(1699999999), b"test-only")\n\n\ndef test_bad_signature():\n    assert not validate_token(1700000060, 1700000000, "invalid", b"test-only")\n',
            'from logic import validate_token\nfrom test_logic import sign\n\n\ndef test_expiry_boundary():\n    assert not validate_token(1700000000, 1700000000, sign(1700000000), b"test-only")\n\n\ndef test_one_second_remaining():\n    assert validate_token(1700000001, 1700000000, sign(1700000001), b"test-only")\n',
        ),
        Demo(
            "retry",
            "Retry amplification",
            "Fix the retry loop that exceeds the requested attempt limit. Add regression coverage for zero attempts and verify.",
            "def request_with_retry(request, attempts):\n    for _ in range(attempts + 1):\n        result = request()\n        if result is not None:\n            return result\n    return None\n",
            "def request_with_retry(request, attempts):\n    for _ in range(max(0, attempts)):\n        result = request()\n        if result is not None:\n            return result\n    return None\n",
            'from logic import request_with_retry\n\n\ndef test_attempt_limit():\n    calls = []\n    assert request_with_retry(lambda: calls.append(1), 3) is None\n    assert len(calls) == 3\n\n\ndef test_success_stops_retries():\n    calls = []\n    def request():\n        calls.append(1)\n        return "ok"\n    assert request_with_retry(request, 3) == "ok"\n    assert len(calls) == 1\n',
            'from logic import request_with_retry\n\n\ndef test_zero_attempts():\n    calls = []\n    assert request_with_retry(lambda: calls.append(1), 0) is None\n    assert not calls\n\n\ndef test_negative_attempts():\n    assert request_with_retry(lambda: "unexpected", -1) is None\n',
        ),
        Demo(
            "sql",
            "N+1 query",
            "Replace per-user database queries with a parameterized batched query. Preserve order, handle empty input, add regression tests and verify.",
            'def load_users(db, ids):\n    users = []\n    for user_id in ids:\n        users.extend(db.execute("SELECT id, name FROM users WHERE id = ?", (user_id,)).fetchall())\n    return users\n',
            'def load_users(db, ids):\n    if not ids:\n        return []\n    placeholders = ",".join("?" for _ in ids)\n    rows = db.execute(f"SELECT id, name FROM users WHERE id IN ({placeholders})", tuple(ids)).fetchall()\n    by_id = {row[0]: row for row in rows}\n    return [by_id[user_id] for user_id in ids if user_id in by_id]\n',
            'import sqlite3\nfrom logic import load_users\n\n\ndef database():\n    db = sqlite3.connect(":memory:")\n    db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT)")\n    db.executemany("INSERT INTO users VALUES (?, ?)", [(1, "A"), (2, "B")])\n    return db\n\n\ndef test_batched_query():\n    db = database()\n    statements = []\n    db.set_trace_callback(statements.append)\n    assert load_users(db, [2, 1]) == [(2, "B"), (1, "A")]\n    assert len(statements) == 1\n\n\ndef test_empty_input():\n    db = database()\n    statements = []\n    db.set_trace_callback(statements.append)\n    assert load_users(db, []) == []\n    assert not statements\n',
            'from logic import load_users\nfrom test_logic import database\n\n\ndef test_duplicate_and_missing_ids():\n    assert load_users(database(), [2, 99, 2]) == [(2, "B"), (2, "B")]\n\n\ndef test_parameterized_input():\n    assert load_users(database(), ["1 OR 1=1"]) == []\n',
        ),
        Demo(
            "traversal",
            "Path traversal",
            "Fix unsafe file path resolution, including sibling prefix and symlink escapes. Add regression tests and verify.",
            'from pathlib import Path\n\n\ndef safe_file(root, name):\n    base = Path(root).resolve()\n    target = (base / name).resolve()\n    if not str(target).startswith(str(base)):\n        raise ValueError("outside root")\n    return target\n',
            'from pathlib import Path\n\n\ndef safe_file(root, name):\n    base = Path(root).resolve()\n    target = (base / name).resolve()\n    if not target.is_relative_to(base):\n        raise ValueError("outside root")\n    return target\n',
            'import pytest\nfrom logic import safe_file\n\n\ndef test_normal_path(tmp_path):\n    assert safe_file(tmp_path, "report.txt") == tmp_path / "report.txt"\n\n\ndef test_sibling_escape(tmp_path):\n    root = tmp_path / "files"\n    root.mkdir()\n    with pytest.raises(ValueError):\n        safe_file(root, "../files-secret/key.txt")\n',
            'import os\nimport pytest\nfrom logic import safe_file\n\n\ndef test_parent_escape(tmp_path):\n    with pytest.raises(ValueError):\n        safe_file(tmp_path, "../../outside.txt")\n\n\n@pytest.mark.skipif(os.name == "nt", reason="Windows symlink privilege varies")\ndef test_symlink_escape(tmp_path):\n    root = tmp_path / "root"\n    root.mkdir()\n    (root / "link").symlink_to(tmp_path)\n    with pytest.raises(ValueError):\n        safe_file(root, "link/outside.txt")\n',
        ),
    ]
}


def prepare_demo(directory: Path, demo_id: str) -> Path:
    demo = DEMOS[demo_id]
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in {"logic.py": demo.source, "test_logic.py": demo.tests}.items():
        (directory / name).write_text(content, encoding="utf-8")
    return directory


def demo_patch(demo: Demo) -> str:
    fix = "".join(
        difflib.unified_diff(
            demo.source.splitlines(keepends=True),
            demo.fixed.splitlines(keepends=True),
            fromfile="a/logic.py",
            tofile="b/logic.py",
        )
    )
    regression = "".join(
        difflib.unified_diff(
            [],
            demo.regression.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile="b/test_regression.py",
        )
    )
    return fix + regression
