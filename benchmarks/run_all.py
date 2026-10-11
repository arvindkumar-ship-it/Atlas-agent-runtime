from __future__ import annotations

import argparse
import asyncio
import csv
import json
import platform
import statistics
import tempfile
from pathlib import Path
from time import perf_counter

from atlas.config import Settings
from atlas.demo import DEMOS, prepare_demo
from atlas.domain.models import Task
from atlas.models.backends import make_backend
from atlas.runtime.engine import RuntimeEngine
from atlas.runtime.inference import InferenceScheduler, estimate_kv_bytes
from atlas.storage.store import Store
from atlas.telemetry.metrics import MetricsRegistry

MODES = [
    "full_history",
    "recent_window",
    "keyword_memory",
    "summary_memory",
    "atlas_context",
    "atlas_runtime",
    "atlas_full",
]


async def run_one(root: Path, task_id: str, mode: str, repetition: int, settings: Settings) -> dict:
    demo = DEMOS[task_id]
    directory = root / f"{task_id}-{mode}-{repetition}"
    repository = prepare_demo(directory / "source", task_id).resolve()
    current = settings.model_copy(
        update={"data_dir": directory / "data", "database_url": f"sqlite:///{directory}/atlas.db"}
    )
    store = Store(current.database_url)
    metrics = MetricsRegistry()
    scheduler = InferenceScheduler(make_backend(current), current, metrics)
    task = Task(
        repository_path=str(repository),
        instruction=demo.instruction,
        demo_id=task_id if current.backend == "demo" else None,
        approval_policy="workspace",
        context_mode=mode,
        context_tokens=24576,
        total_tokens=1_000_000,
    )
    run = store.create(task)
    started = perf_counter()
    error = None
    try:
        run = await RuntimeEngine(store, current, scheduler, metrics).execute(run.id)
    except Exception as exc:
        error = str(exc)
        run = store.get(run.id)
    elapsed = (perf_counter() - started) * 1000
    events = store.event_list(run.id)
    contexts = [event["payload"] for event in events if event["type"] == "context.compiled"]
    models = [event["payload"] for event in events if event["type"] == "model.completed"]
    tools = [event["payload"] for event in events if event["type"] == "tool.completed"]
    # Held-out exact fixture oracle, independent of the agent's success declaration.
    patch_correct = bool(
        run.workspace and (Path(run.workspace) / "logic.py").read_text() == demo.fixed
    )
    regression_added = bool(run.workspace and (Path(run.workspace) / "test_regression.py").exists())
    raw = sum(item["raw_tokens_estimate"] for item in contexts)
    compiled = sum(item["compiled_tokens_estimate"] for item in contexts)
    result = {
        "task_id": task_id,
        "mode": mode,
        "repetition": repetition,
        "run_id": run.id,
        "backend": current.backend,
        "model": current.model_name if current.backend != "demo" else None,
        "hardware": platform.platform(),
        "cache_state": "cold",
        "seed": 42,
        "success": run.status == "completed" and patch_correct and regression_added,
        "patch_correct": patch_correct,
        "tests_passed": bool(run.report and run.report["passed"]),
        "workflow_latency_ms": elapsed,
        "model_calls": len(models),
        "tool_calls": len(tools),
        "model_latency_ms": sum(item["latency_ms"] for item in models),
        "tool_wait_ms": sum(item["duration_ms"] for item in tools),
        "raw_tokens_estimate": raw,
        "compiled_tokens_estimate": compiled,
        "context_reduction": 1 - compiled / raw if raw else None,
        "input_tokens": sum(item["input_tokens"] for item in models)
        if models and all(item["input_tokens"] is not None for item in models)
        else None,
        "output_tokens": sum(item["output_tokens"] for item in models)
        if models and all(item["output_tokens"] is not None for item in models)
        else None,
        "ttft_ms": statistics.mean(
            item["ttft_ms"] for item in models if item["ttft_ms"] is not None
        )
        if any(item["ttft_ms"] is not None for item in models)
        else None,
        "tpot_ms": statistics.mean(
            item["tpot_ms"] for item in models if item["tpot_ms"] is not None
        )
        if any(item["tpot_ms"] is not None for item in models)
        else None,
        "peak_kv_bytes_estimate": max(
            (
                estimate_kv_bytes(scheduler.kv.config, item["compiled_tokens_estimate"] + 1024)
                for item in contexts
            ),
            default=0,
        ),
        "gpu_memory_bytes": None,
        "error": error or run.error,
    }
    await scheduler.close()
    store.close()
    return result


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    left, fraction = int(position), position % 1
    return ordered[left] * (1 - fraction) + ordered[min(left + 1, len(ordered) - 1)] * fraction


def summarize(rows: list[dict], target: Path):
    summary = []
    for mode in sorted({row["mode"] for row in rows}):
        subset = [row for row in rows if row["mode"] == mode]
        raw = sum(row["raw_tokens_estimate"] for row in subset)
        compiled = sum(row["compiled_tokens_estimate"] for row in subset)
        summary.append(
            {
                "mode": mode,
                "runs": len(subset),
                "success_rate": statistics.mean(row["success"] for row in subset),
                "latency_median_ms": statistics.median(
                    row["workflow_latency_ms"] for row in subset
                ),
                "latency_p95_ms": percentile([row["workflow_latency_ms"] for row in subset], 0.95),
                "input_tokens_estimate_mean": statistics.mean(
                    row["compiled_tokens_estimate"] for row in subset
                ),
                "context_reduction": 1 - compiled / raw if raw else 0,
                "kv_peak_estimate_mean": statistics.mean(
                    row["peak_kv_bytes_estimate"] for row in subset
                ),
            }
        )
    with target.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    return summary


async def run(args):
    settings = Settings.from_env()
    if args.trusted_local:
        settings = settings.model_copy(update={"sandbox": "local", "allow_trusted_local": True})
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with (
        tempfile.TemporaryDirectory(prefix="atlas-benchmark-") as directory,
        output.open("w", encoding="utf-8") as stream,
    ):
        # Rotate mode order between repetitions to reduce order bias.
        for repetition in range(args.repetitions):
            order = (
                args.modes[repetition % len(args.modes) :]
                + args.modes[: repetition % len(args.modes)]
            )
            for mode in order:
                for task_id in args.tasks:
                    result = await run_one(Path(directory), task_id, mode, repetition, settings)
                    rows.append(result)
                    stream.write(json.dumps(result) + "\n")
                    stream.flush()
                    print(
                        f"{mode:16} {task_id:10} repeat={repetition + 1} success={result['success']} {result['workflow_latency_ms']:.0f} ms",
                        flush=True,
                    )
    summary = summarize(rows, output.with_name("summary.csv"))
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Measured Atlas fixture/runtime benchmark")
    parser.add_argument("--tasks", nargs="+", choices=DEMOS, default=list(DEMOS))
    parser.add_argument("--modes", nargs="+", choices=MODES, default=MODES)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", default="benchmarks/results/results.jsonl")
    parser.add_argument("--trusted-local", action="store_true")
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
