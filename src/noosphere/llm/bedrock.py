"""Bedrock Mantle client wrapper with spend metering.

Thinking parameters (per Anthropic SDK docs, anthropic 1.x):
- Opus 5: thinking is on by default (adaptive) — the ``thinking`` parameter is
  omitted entirely.
- Haiku 4.5 (pre-4.6 rules): thinking requires an explicit
  ``{"type": "enabled", "budget_tokens": N}``; extraction work does not need
  thinking, so the parameter is omitted there too (no thinking).
"""

import asyncio
import json
import re
import threading
from typing import Any

from noosphere.config import Settings

# List prices, USD per million tokens (input, output). The meter's est_usd is
# an ESTIMATE from these list prices, not billing data.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    Settings.opus_model: (5.0, 25.0),
    Settings.haiku_model: (1.0, 5.0),
}
PRICING_NOTE = (
    "est_usd is an estimate computed from list prices "
    "(opus $5/$25, haiku $1/$5 per MTok), not actual billing."
)


def _price_for(model: str) -> tuple[float, float]:
    if model in PRICES_PER_MTOK:
        return PRICES_PER_MTOK[model]
    if "opus" in model:
        return 5.0, 25.0
    if "haiku" in model:
        return 1.0, 5.0
    return 0.0, 0.0


class SpendMeter:
    """Thread-safe token accumulator with estimated-cost totals."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, list[int]] = {}

    def record(self, model: str, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            counts = self._counts.setdefault(model, [0, 0])
            counts[0] += input_tokens
            counts[1] += output_tokens

    def totals(self) -> dict:
        with self._lock:
            snapshot = {m: (c[0], c[1]) for m, c in self._counts.items()}
        models: dict[str, dict[str, float | int]] = {}
        grand_in = grand_out = 0
        grand_usd = 0.0
        for model, (inp, out) in snapshot.items():
            in_price, out_price = _price_for(model)
            est = inp / 1_000_000 * in_price + out / 1_000_000 * out_price
            models[model] = {"input": inp, "output": out, "est_usd": est}
            grand_in += inp
            grand_out += out
            grand_usd += est
        return {
            "models": models,
            "total": {"input": grand_in, "output": grand_out, "est_usd": grand_usd},
            "note": PRICING_NOTE,
        }


class LlmRefusal(Exception):
    """The model declined the request (stop_reason == "refusal")."""


_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def _parse_json(text: str) -> Any:
    stripped = text.strip()
    if match := _FENCE_RE.match(stripped):
        stripped = match.group(1).strip()
    return json.loads(stripped)


class LlmClient:
    """Async wrapper over ``anthropic.AnthropicBedrock``.

    Uses the classic bedrock-runtime (InvokeModel) client, not the newer
    Mantle endpoint: probed 2026-08-22, this account's entitlements cover the
    classic path (with ``us.`` inference-profile ids) while every model 403s
    on Mantle. Revisit once Mantle access is enabled on the account.

    The real client is constructed lazily on first use; ``transport`` injection
    replaces it for tests (anything exposing a compatible ``messages.create``).
    """

    def __init__(
        self,
        region: str,
        meter: SpendMeter,
        transport: Any = None,
        haiku_model: str | None = None,
        opus_model: str | None = None,
    ) -> None:
        self._region = region
        self.meter = meter
        self._transport = transport
        # Configured model ids (Settings screen) — the Settings class attrs are
        # only the last-resort default, not the live user configuration.
        self._haiku_model = haiku_model or Settings.haiku_model
        self._opus_model = opus_model or Settings.opus_model

    def _client(self) -> Any:
        if self._transport is None:
            import anthropic

            self._transport = anthropic.AnthropicBedrock(aws_region=self._region)
        return self._transport

    async def _request_text(
        self, model: str, system: str, user: str, max_tokens: int
    ) -> str:
        client = self._client()
        response = await asyncio.to_thread(
            client.messages.create,
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.meter.record(model, usage.input_tokens, usage.output_tokens)
        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            explanation = getattr(details, "explanation", None)
            raise LlmRefusal(
                f"model {model} refused the request"
                f" (category={category!r}, explanation={explanation!r})"
            )
        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text
        raise ValueError(f"no text block in response from {model}")

    async def _request_json(
        self, model: str, system: str, user: str, max_tokens: int
    ) -> Any:
        return _parse_json(await self._request_text(model, system, user, max_tokens))

    async def haiku_json(
        self, system: str, user: str, max_tokens: int = 2048, model: str | None = None
    ) -> dict:
        return await self._request_json(
            model or self._haiku_model, system, user, max_tokens
        )

    async def opus_json(
        self, system: str, user: str, max_tokens: int = 8192, model: str | None = None
    ) -> dict:
        return await self._request_json(
            model or self._opus_model, system, user, max_tokens
        )

    async def opus_text(
        self, system: str, user: str, max_tokens: int = 8192, model: str | None = None
    ) -> str:
        return await self._request_text(
            model or self._opus_model, system, user, max_tokens
        )
