"""Shared LLM cost tracking for call_llm — QuantAI workspace integration.

Every model call routed through ``src/utils/llm.py::call_llm`` records one
event in the suite-wide cost log (``quantai_core.cost`` → ``~/.quantai/costs/``,
see the monorepo's docs/architecture/decisions/0008-shared-cost-tracking.md)
so this app's spend shows up on the Costs dashboard alongside the rest of the
suite. Implemented as a LangChain callback because ``call_llm`` wraps models
with ``with_structured_output(json_mode)``, which discards ``usage_metadata``
from the return value — the callback sees it regardless.

Guarded like the other quantai_core integrations in this repo (e.g. the
shared-catalog import in ``src/llm/models.py``): a missing/broken
quantai-core degrades to a no-op, never breaks a run.
"""

from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler

try:
    from quantai_core.cost import record as _record_cost
    from quantai_core.cost.usage import from_langchain_usage_metadata
except Exception:  # pragma: no cover - exercised only when quantai-core absent
    _record_cost = None
    from_langchain_usage_metadata = None

# This repo's ModelProvider display names -> the shared catalog's provider ids
# (quantai_core.cost.pricing keys on the catalog ids).
_PROVIDER_IDS = {
    "anthropic": "anthropic",
    "openai": "openai",
    "azure openai": "openai",
    "google": "google",
    "gemini": "google",
}


def _provider_id(model_provider: str) -> str:
    normalized = (model_provider or "").strip().lower()
    return _PROVIDER_IDS.get(normalized, normalized or "unknown")


class CostRecordingHandler(BaseCallbackHandler):
    """Records one shared cost event per LLM call. Never raises."""

    def __init__(self, model_name: str, model_provider: str, agent_name: str | None):
        self._model = model_name
        self._provider = _provider_id(model_provider)
        self._agent = agent_name

    def on_llm_end(self, response, **kwargs) -> None:  # noqa: ANN001 - LangChain signature
        if _record_cost is None or from_langchain_usage_metadata is None:
            return
        try:
            message = response.generations[0][0].message
            usage = getattr(message, "usage_metadata", None)
            if not usage:
                return
            u = from_langchain_usage_metadata(usage)
            _record_cost(
                "ai-hedge-fund",
                self._provider,
                self._model,
                u.input_tokens,
                u.output_tokens,
                category="hedge-fund.agent-run",
                cache_read=u.cache_read_tokens,
                cache_write=u.cache_write_tokens,
                metadata={"agent": self._agent} if self._agent else None,
            )
        except Exception:  # noqa: BLE001 - cost tracking must never break a run
            pass


def cost_callbacks(model_name: str, model_provider: str, agent_name: str | None) -> list:
    """Callbacks list for llm.invoke(config={"callbacks": ...}); empty when
    the shared cost module is unavailable."""
    if _record_cost is None:
        return []
    return [CostRecordingHandler(model_name, model_provider, agent_name)]
