from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

from atlas.domain.models import ToolResult
from atlas.security.paths import safe_environment


async def run_process(
    argv: list[str],
    cwd: Path,
    timeout: float = 45,
    input_data: bytes | None = None,
    env: dict | None = None,
) -> ToolResult:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env or safe_environment(),
        stdin=asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=os.name != "nt",
    )
    chunks, saved, truncated = [], 0, False

    async def collect():
        nonlocal saved, truncated
        if input_data is not None:
            process.stdin.write(input_data)
            await process.stdin.drain()
            process.stdin.close()
        while chunk := await process.stdout.read(8192):
            keep = min(len(chunk), 128000 - saved)
            if keep:
                chunks.append(chunk[:keep])
                saved += keep
            if keep < len(chunk):
                truncated = True
        await process.wait()

    try:
        await asyncio.wait_for(collect(), timeout=timeout)
    except (TimeoutError, asyncio.CancelledError) as exc:
        if process.returncode is None:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        await process.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        return ToolResult(
            ok=False,
            output=b"".join(chunks).decode(errors="replace"),
            error="Process timed out",
            retryable=False,
        )
    output = b"".join(chunks).decode(errors="replace")
    if truncated:
        output += "\n[output truncated; verification is inconclusive]"
    return ToolResult(
        ok=process.returncode == 0 and not truncated,
        output=output,
        exit_code=process.returncode,
        error="Output limit exceeded"
        if truncated
        else (None if process.returncode == 0 else "Process returned nonzero status"),
    )
