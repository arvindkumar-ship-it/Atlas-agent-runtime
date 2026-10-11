import json

import httpx
import pytest

from atlas.models.backends import CompatibleBackend, parse_decision
from atlas.models.types import GenerateRequest
from atlas.telemetry.metrics import MetricsRegistry


def stream(packets):
    return (
        "".join("data: " + json.dumps(packet) + "\n\n" for packet in packets) + "data: [DONE]\n\n"
    )


@pytest.mark.asyncio
async def test_streaming_adapter_preserves_server_usage(settings):
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["stream_options"]["include_usage"]
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            text=stream(
                [
                    {"choices": [{"delta": {"content": '{"type":"tool",'}}]},
                    {"choices": [{"delta": {"content": '"actions":[{"tool":"list_files"}]}'}}]},
                    {
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 81,
                            "completion_tokens": 17,
                            "prompt_tokens_details": {"cached_tokens": 32},
                        },
                    },
                ]
            ),
        )

    backend = CompatibleBackend(settings, httpx.MockTransport(handler))
    try:
        result = await backend.generate(
            GenerateRequest(messages=[{"role": "user", "content": "repair"}])
        )
        assert parse_decision(result.text).actions[0].tool == "list_files"
        assert (result.input_tokens, result.output_tokens, result.cached_tokens) == (81, 17, 32)
        assert result.ttft_ms is not None and result.tpot_ms is not None
        assert result.usage_source == "server"
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_missing_usage_is_unknown_not_zero(settings):
    backend = CompatibleBackend(
        settings,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                text=stream(
                    [
                        {"choices": [{"delta": {"content": "{}"}}]},
                    ]
                ),
            )
        ),
    )
    try:
        result = await backend.generate(GenerateRequest(messages=[]))
        assert result.input_tokens is None and result.output_tokens is None
        assert result.cached_tokens is None and result.tpot_ms is None
        assert result.usage_source == "unavailable"
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_native_tool_call_chunks_are_reassembled(settings):
    packets = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"name": "read_file", "arguments": '{"path":'}}
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"logic.py"}'}}]}}
            ]
        },
    ]
    backend = CompatibleBackend(
        settings, httpx.MockTransport(lambda _: httpx.Response(200, text=stream(packets)))
    )
    try:
        decision = parse_decision((await backend.generate(GenerateRequest(messages=[]))).text)
        assert decision.actions[0].arguments == {"path": "logic.py"}
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_adapter_http_error_is_not_success(settings):
    backend = CompatibleBackend(settings, httpx.MockTransport(lambda _: httpx.Response(503)))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await backend.generate(GenerateRequest(messages=[]))
    finally:
        await backend.close()


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "```json\n{}\n```",
        '{"type":"finish","summary":""}',
        '{"type":"tool","actions":[],"unknown":"ignored"}',
    ],
)
def test_bad_decisions_are_rejected(text):
    with pytest.raises(ValueError):
        parse_decision(text)


def test_prometheus_histogram_is_cumulative():
    metrics = MetricsRegistry()
    for value in [2, 8, 40]:
        metrics.observe("atlas_test_ms", value)
    buckets = {
        sample["labels"]["le"]: sample["value"]
        for sample in metrics.snapshot()["samples"]
        if sample["name"] == "atlas_test_ms_bucket"
    }
    assert (buckets["5.0"], buckets["10.0"], buckets["50.0"], buckets["+Inf"]) == (1, 2, 3, 3)
    assert b"atlas_test_ms_count 3.0" in metrics.prometheus()


@pytest.mark.parametrize(
    "operation",
    [
        lambda m: m.observe("x", float("nan")),
        lambda m: m.set_gauge("x", float("inf")),
        lambda m: m.increment("x", -1),
        lambda m: m.increment("x", labels={"run_id": "unique"}),
    ],
)
def test_metrics_reject_invalid_values_or_unbounded_keys(operation):
    with pytest.raises(ValueError):
        operation(MetricsRegistry())
