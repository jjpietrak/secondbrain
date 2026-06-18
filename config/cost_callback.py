"""LiteLLM custom callback: append every routed call to the Second Brain cost ledger.

Loaded by the proxy via `litellm_settings.callbacks: cost_callback.proxy_handler_instance`.
LiteLLM resolves callback modules RELATIVE TO THIS CONFIG DIRECTORY, so this file must
live next to litellm.yaml. The actual ledger logic lives in agents/cost_tracker.py.
"""
from __future__ import annotations
import os, sys

# Make the repo root importable so `agents.cost_tracker` resolves under the proxy.
_CODE = os.environ.get("CODE_PATH", "/home/jpietrak/second_brain")
if _CODE not in sys.path:
    sys.path.insert(0, _CODE)

from litellm.integrations.custom_logger import CustomLogger  # noqa: E402
from agents import cost_tracker  # noqa: E402


def _provider_of(model: str) -> str:
    m = (model or "").lower()
    if "ollama" in m:
        return "ollama"
    if "gemini" in m or "google" in m:
        return "gemini"
    if "claude" in m or "anthropic" in m:
        return "anthropic"
    return "unknown"


def _usage_dict(response_obj) -> dict:
    try:
        u = response_obj.get("usage") if isinstance(response_obj, dict) else getattr(response_obj, "usage", None)
        if u is None:
            return {}
        if isinstance(u, dict):
            return u
        if hasattr(u, "model_dump"):
            return u.model_dump()
        if hasattr(u, "dict"):
            return u.dict()
        return {"prompt_tokens": getattr(u, "prompt_tokens", 0),
                "completion_tokens": getattr(u, "completion_tokens", 0)}
    except Exception:
        return {}


class CostLedgerLogger(CustomLogger):
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._log(kwargs, response_obj)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._log(kwargs, response_obj)

    def _log(self, kwargs, response_obj):
        try:
            lp = kwargs.get("litellm_params", {}) or {}
            underlying = lp.get("model") or kwargs.get("model") or ""
            meta = lp.get("metadata", {}) or {}
            role = meta.get("model_group") or kwargs.get("model") or underlying
            # Prefer the explicit provider; LiteLLM strips the "ollama/" prefix from model.
            provider = (lp.get("custom_llm_provider") or "").lower() or _provider_of(underlying)
            if provider in ("google", "vertex_ai", "gemini"):
                provider = "gemini"
            elif provider not in ("ollama", "anthropic"):
                provider = _provider_of(underlying)
            usage = _usage_dict(response_obj)
            cost = kwargs.get("response_cost") or 0.0
            source = "local-free" if provider == "ollama" else "pay-as-you-go"
            cost_tracker.record(
                action="litellm",
                role=str(role),
                provider=provider,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                cost_usd=float(cost or 0.0),
                source=source,
            )
        except Exception as e:  # never break a request because logging failed
            print(f"[cost_callback] failed to log: {e}", file=sys.stderr)


proxy_handler_instance = CostLedgerLogger()
