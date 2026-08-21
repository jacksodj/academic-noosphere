"""llm module tests: fake transport, JSON parsing, metering, refusal, prompts."""

import threading
from dataclasses import dataclass, field
from typing import Any

import pytest

from noosphere.config import Settings
from noosphere.llm.bedrock import LlmClient, LlmRefusal, SpendMeter, _parse_json
from noosphere.llm.prompts import gap_statement_prompt, narrative_extraction_prompt

OPUS = Settings.opus_model
HAIKU = Settings.haiku_model


@dataclass
class _Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class _TextBlock:
    text: str
    type: str = "text"


@dataclass
class _StopDetails:
    category: str | None = None
    explanation: str | None = None


@dataclass
class _Response:
    content: list[Any]
    stop_reason: str = "end_turn"
    usage: _Usage = field(default_factory=lambda: _Usage(100, 50))
    stop_details: _StopDetails | None = None


def _text_response(text: str, **kwargs: Any) -> _Response:
    return _Response(content=[_TextBlock(text)], **kwargs)


class _Messages:
    def __init__(self, transport: "FakeTransport") -> None:
        self._transport = transport

    def create(self, **kwargs: Any) -> _Response:
        self._transport.calls.append(kwargs)
        return self._transport.responses.pop(0)


class FakeTransport:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.messages = _Messages(self)


def _client(responses: list[_Response], meter: SpendMeter | None = None) -> LlmClient:
    return LlmClient("us-east-1", meter or SpendMeter(), transport=FakeTransport(responses))


async def test_haiku_json_plain() -> None:
    client = _client([_text_response('{"claims": [{"quote": "q", "source_index": 0}]}')])
    result = await client.haiku_json("sys", "usr")
    assert result == {"claims": [{"quote": "q", "source_index": 0}]}


async def test_json_strips_markdown_fences() -> None:
    fenced = '```json\n{"statement": "s", "kinds": ["narrative"], "cited": [1]}\n```'
    client = _client([_text_response(fenced)])
    result = await client.opus_json("sys", "usr")
    assert result == {"statement": "s", "kinds": ["narrative"], "cited": [1]}


def test_parse_json_fence_without_language_tag() -> None:
    assert _parse_json('```\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json('  {"a": 1}  ') == {"a": 1}


async def test_opus_text_returns_raw_text() -> None:
    client = _client([_text_response("plain prose, not JSON")])
    assert await client.opus_text("sys", "usr") == "plain prose, not JSON"


async def test_request_shape_model_ids_and_no_thinking_param() -> None:
    transport = FakeTransport([_text_response("{}"), _text_response("{}")])
    client = LlmClient("us-east-1", SpendMeter(), transport=transport)
    await client.haiku_json("sys-h", "usr-h")
    await client.opus_json("sys-o", "usr-o", max_tokens=4096)

    haiku_call, opus_call = transport.calls
    assert haiku_call["model"] == HAIKU
    assert opus_call["model"] == OPUS
    assert opus_call["max_tokens"] == 4096
    assert haiku_call["system"] == "sys-h"
    assert haiku_call["messages"] == [{"role": "user", "content": "usr-h"}]
    for call in transport.calls:
        assert "thinking" not in call


async def test_meter_accumulation_and_est_usd() -> None:
    meter = SpendMeter()
    transport = FakeTransport(
        [
            _text_response("{}", usage=_Usage(1_000_000, 200_000)),
            _text_response("{}", usage=_Usage(500_000, 100_000)),
            _text_response("{}", usage=_Usage(2_000_000, 400_000)),
        ]
    )
    client = LlmClient("us-east-1", meter, transport=transport)
    await client.opus_json("s", "u")
    await client.opus_json("s", "u")
    await client.haiku_json("s", "u")

    totals = meter.totals()
    opus = totals["models"][OPUS]
    assert opus["input"] == 1_500_000
    assert opus["output"] == 300_000
    assert opus["est_usd"] == pytest.approx(1.5 * 5.0 + 0.3 * 25.0)
    haiku = totals["models"][HAIKU]
    assert haiku["est_usd"] == pytest.approx(2.0 * 1.0 + 0.4 * 5.0)
    assert totals["total"]["input"] == 3_500_000
    assert totals["total"]["est_usd"] == pytest.approx(
        opus["est_usd"] + haiku["est_usd"]
    )
    assert "estimate" in totals["note"]


def test_meter_thread_safety() -> None:
    meter = SpendMeter()
    threads = [
        threading.Thread(
            target=lambda: [meter.record(OPUS, 10, 5) for _ in range(1000)]
        )
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    totals = meter.totals()
    assert totals["models"][OPUS]["input"] == 8 * 1000 * 10
    assert totals["models"][OPUS]["output"] == 8 * 1000 * 5


async def test_refusal_raises_llm_refusal() -> None:
    response = _text_response(
        "",
        stop_reason="refusal",
        stop_details=_StopDetails(category="cyber", explanation="declined"),
    )
    client = _client([response])
    with pytest.raises(LlmRefusal, match="cyber"):
        await client.opus_text("sys", "usr")


async def test_refusal_still_records_usage() -> None:
    meter = SpendMeter()
    response = _text_response("", stop_reason="refusal", usage=_Usage(42, 7))
    client = _client([response], meter)
    with pytest.raises(LlmRefusal):
        await client.haiku_json("sys", "usr")
    assert meter.totals()["models"][HAIKU]["input"] == 42


def test_narrative_extraction_prompt_contains_inputs() -> None:
    snippets = [
        {"text": "Future work should examine consolidation.", "work_id": "W123"},
        {"text": "A limitation is the small corpus.", "work_id": "W456"},
    ]
    system, user = narrative_extraction_prompt(snippets)
    assert isinstance(system, str) and isinstance(user, str)
    assert "quote" in system and "source_index" in system
    assert "Future work should examine consolidation." in user
    assert "W456" in user
    assert '"index": 0' in user and '"index": 1' in user


def test_gap_statement_prompt_contains_inputs() -> None:
    candidate = '{"whitespace_id": "ws-1", "description": "sparse bridge"}'
    evidence = '[{"kind": "work", "work_id": "W789", "quote": "gap here"}]'
    system, user = gap_statement_prompt(candidate, evidence)
    assert isinstance(system, str) and isinstance(user, str)
    assert "statement" in system and "kinds" in system and "cited" in system
    assert candidate in user
    assert evidence in user
