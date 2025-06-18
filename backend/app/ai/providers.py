"""Provider agnostic LLM adapters.

The platform talks to language models through a single small interface so that
new providers can be added without touching the application logic.  Six
provider *kinds* are supported:

``openai``      OpenAI (and OpenAI-compatible gateways such as Azure)
``anthropic``   Anthropic Claude (Messages API)
``openrouter``  OpenRouter (OpenAI-compatible gateway, many models)
``custom``      any OpenAI-compatible endpoint (self-hosted gateways, proxies)
``ollama``      local Ollama server
``mock``        deterministic offline provider used by the tests and the
                "no credentials" mode of the application

All HTTP traffic is issued **from the backend only** — API keys never reach
the browser.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

PROVIDER_KINDS = ("openai", "anthropic", "openrouter", "custom", "ollama", "mock")

PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI",
    "anthropic": "Anthropic Claude",
    "openrouter": "OpenRouter",
    "custom": "Custom (OpenAI-compatible)",
    "ollama": "Ollama (local)",
    "mock": "Offline demo provider",
}

PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "o4-mini"],
        "requires_key": True,
        "hosted": True,
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-3-5-haiku-latest",
        "models": [
            "claude-3-5-haiku-latest",
            "claude-3-5-sonnet-latest",
            "claude-3-7-sonnet-latest",
        ],
        "requires_key": True,
        "hosted": True,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "models": ["openai/gpt-4o-mini", "anthropic/claude-3.5-haiku", "google/gemini-flash-1.5"],
        "requires_key": True,
        "hosted": True,
    },
    "custom": {
        "base_url": "",
        "model": "",
        "models": [],
        "requires_key": False,
        "hosted": False,
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.1",
        "models": ["llama3.1", "llama3.2", "mistral", "qwen2.5"],
        "requires_key": False,
        "hosted": False,
    },
    "mock": {
        "base_url": "",
        "model": "offline-deterministic",
        "models": ["offline-deterministic"],
        "requires_key": False,
        "hosted": False,
    },
}


@dataclass
class ProviderConfig:
    kind: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 45.0
    max_tokens: int = 2000
    temperature: float = 0.2
    max_retries: int = 2

    def masked_key(self) -> str | None:
        if not self.api_key:
            return None
        if len(self.api_key) <= 10:
            return "***"
        return f"{self.api_key[:5]}…{self.api_key[-4:]}"


@dataclass
class CompletionResult:
    text: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class ConnectionTest:
    ok: bool
    message: str
    latency_ms: float = 0.0
    models: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    """Raised when a provider call fails."""


class ProviderAdapter(Protocol):
    kind: str

    def list_models(self, config: ProviderConfig) -> list[str]: ...

    def test_connection(self, config: ProviderConfig) -> ConnectionTest: ...

    def complete(
        self, config: ProviderConfig, system_prompt: str, user_prompt: str
    ) -> CompletionResult: ...


class _HTTPAdapter:
    kind = "custom"

    def list_models(self, config: ProviderConfig) -> list[str]:
        defaults = PROVIDER_DEFAULTS.get(self.kind, {})
        if defaults.get("requires_key") and not config.api_key:
            raise ProviderError(
                "No API key configured for this provider. Add a key in AI settings first."
            )
        base = (config.base_url or defaults.get("base_url") or "").rstrip("/")
        if not base:
            return list(defaults.get("models", []))
        try:
            with httpx.Client(timeout=config.timeout_seconds) as client:
                response = client.get(f"{base}/models", headers=self._headers(config))
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach the provider: {exc}") from exc
        data = payload.get("data") or payload.get("models") or []
        models: list[str] = []
        for item in data:
            if isinstance(item, dict):
                name = item.get("id") or item.get("name") or item.get("model")
                if name:
                    models.append(str(name))
            elif isinstance(item, str):
                models.append(item)
        return models

    def _headers(self, config: ProviderConfig) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        return headers

    def test_connection(self, config: ProviderConfig) -> ConnectionTest:
        import time

        started = time.perf_counter()
        try:
            models = self.list_models(config)
        except ProviderError as exc:
            return ConnectionTest(
                ok=False, message=str(exc), latency_ms=(time.perf_counter() - started) * 1000
            )
        latency = (time.perf_counter() - started) * 1000
        return ConnectionTest(
            ok=True,
            message=f"Connected to {PROVIDER_LABELS.get(self.kind, self.kind)}.",
            latency_ms=latency,
            models=models[:50],
        )

    def complete(
        self, config: ProviderConfig, system_prompt: str, user_prompt: str
    ) -> CompletionResult:
        raise NotImplementedError


class OpenAICompatibleAdapter(_HTTPAdapter):
    """OpenAI, OpenRouter and any custom OpenAI-compatible endpoint."""

    kind = "openai"

    def complete(
        self, config: ProviderConfig, system_prompt: str, user_prompt: str
    ) -> CompletionResult:
        import time

        base = (config.base_url or PROVIDER_DEFAULTS[self.kind]["base_url"]).rstrip("/")
        payload = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = self._headers(config)
        if self.kind == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/intelligent-production-scheduling"
            headers["X-Title"] = "Intelligent Production Scheduling AI"
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=config.timeout_seconds) as client:
                response = client.post(f"{base}/chat/completions", json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:400]
            raise ProviderError(
                f"{PROVIDER_LABELS.get(self.kind, self.kind)} returned HTTP "
                f"{exc.response.status_code}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Could not reach {PROVIDER_LABELS.get(self.kind, self.kind)}: {exc}"
            ) from exc
        latency = (time.perf_counter() - started) * 1000
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("The provider returned an unexpected response shape.") from exc
        finish_reason = choice.get("finish_reason")
        usage = data.get("usage") or {}
        completion_tokens = int(usage.get("completion_tokens", 0))
        if not (text or "").strip():
            # A model that stops because it ran out of output tokens usually
            # returns an empty body.  Saying so explicitly turns a confusing
            # empty answer into an actionable instruction for the planner.
            if finish_reason == "length":
                raise ProviderError(
                    f"The model used all {config.max_tokens} output tokens before it "
                    f"finished the answer. Increase the 'max tokens' of this provider "
                    f"and try again."
                )
            raise ProviderError(
                "The provider returned an empty answer. Check the model name and "
                "the provider's own quota or rate limit."
            )
        return CompletionResult(
            text=text or "",
            model=data.get("model", config.model),
            provider=self.kind,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=completion_tokens,
            latency_ms=latency,
            raw={
                "id": data.get("id"),
                "finish_reason": finish_reason,
            },
        )


class OpenRouterAdapter(OpenAICompatibleAdapter):
    kind = "openrouter"


class CustomAdapter(OpenAICompatibleAdapter):
    kind = "custom"


class OllamaAdapter(OpenAICompatibleAdapter):
    """Local Ollama — uses the OpenAI-compatible endpoint when available."""

    kind = "ollama"

    def list_models(self, config: ProviderConfig) -> list[str]:
        base = (config.base_url or PROVIDER_DEFAULTS["ollama"]["base_url"]).rstrip("/")
        root = base[:-3] if base.endswith("/v1") else base
        try:
            with httpx.Client(timeout=config.timeout_seconds) as client:
                response = client.get(f"{root}/api/tags")
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Could not reach the local Ollama server at {root}: {exc}"
            ) from exc
        return [str(item.get("name")) for item in data.get("models", []) if item.get("name")]


class AnthropicAdapter(_HTTPAdapter):
    kind = "anthropic"

    def _headers(self, config: ProviderConfig) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if config.api_key:
            headers["x-api-key"] = config.api_key
        return headers

    def complete(
        self, config: ProviderConfig, system_prompt: str, user_prompt: str
    ) -> CompletionResult:
        import time

        base = (config.base_url or PROVIDER_DEFAULTS["anthropic"]["base_url"]).rstrip("/")
        payload = {
            "model": config.model,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=config.timeout_seconds) as client:
                response = client.post(
                    f"{base}/messages", json=payload, headers=self._headers(config)
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:400]
            raise ProviderError(
                f"Anthropic returned HTTP {exc.response.status_code}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Could not reach Anthropic: {exc}") from exc
        latency = (time.perf_counter() - started) * 1000
        parts = [
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        ]
        usage = data.get("usage") or {}
        text = "\n".join(parts)
        if not text.strip():
            stop_reason = data.get("stop_reason")
            if stop_reason == "max_tokens":
                raise ProviderError(
                    f"The model used all {config.max_tokens} output tokens before it "
                    f"finished the answer. Increase the 'max tokens' of this provider "
                    f"and try again."
                )
            raise ProviderError(
                "The provider returned an empty answer. Check the model name and "
                "the provider's own quota or rate limit."
            )
        return CompletionResult(
            text=text,
            model=data.get("model", config.model),
            provider=self.kind,
            prompt_tokens=int(usage.get("input_tokens", 0)),
            completion_tokens=int(usage.get("output_tokens", 0)),
            latency_ms=latency,
            raw={"id": data.get("id"), "stop_reason": data.get("stop_reason")},
        )


class MockAdapter(_HTTPAdapter):
    """Deterministic offline provider.

    It never invents numbers: it echoes the structured evidence it is given.
    This keeps the "AI" features demonstrable without any credential while
    making it obvious in the UI that the answer came from the offline engine.
    """

    kind = "mock"

    def list_models(self, config: ProviderConfig) -> list[str]:
        return ["offline-deterministic"]

    def test_connection(self, config: ProviderConfig) -> ConnectionTest:
        return ConnectionTest(
            ok=True,
            message="Offline provider ready (no external API call is made).",
            latency_ms=0.1,
            models=["offline-deterministic"],
        )

    def complete(
        self, config: ProviderConfig, system_prompt: str, user_prompt: str
    ) -> CompletionResult:
        evidence: dict[str, Any] = {}
        try:
            start = user_prompt.find("{")
            end = user_prompt.rfind("}")
            if start != -1 and end > start:
                evidence = json.loads(user_prompt[start : end + 1])
        except (ValueError, TypeError):
            evidence = {}
        summary = evidence.get("summary", {})
        orders = evidence.get("orders", [])
        late_orders = [o for o in orders if o.get("is_late")]
        validation = evidence.get("validation", {})
        question = (evidence.get("question") or "").lower()

        headline = (
            f"The plan schedules {summary.get('operations_scheduled')} of "
            f"{summary.get('operations_total')} operations with "
            f"{summary.get('on_time_delivery_pct')}% on-time delivery and a weighted "
            f"tardiness of {summary.get('weighted_tardiness')} minutes."
        )
        bullets: list[str] = []
        recommendations: list[str] = []

        if "late" in question or "delay" in question or "risk" in question:
            if late_orders:
                worst = sorted(late_orders, key=lambda o: -(o.get("tardiness_minutes") or 0))[:5]
                bullets.append(
                    f"{len(late_orders)} of {len(orders)} scheduled orders finish after "
                    "their due date."
                )
                for order in worst:
                    bullets.append(
                        f"{order.get('work_order_code')} (priority {order.get('priority')}) "
                        f"completes {order.get('tardiness_minutes')} minutes late."
                    )
                recommendations.append(
                    "Move the most urgent late orders to the front of the queue, or relax the "
                    "due date if the customer allows it."
                )
            else:
                bullets.append("No scheduled order finishes after its due date.")
        elif "bottleneck" in question or "machine" in question or "utilis" in question:
            code = summary.get("bottleneck_machine_code")
            if code:
                bullets.append(
                    f"{code} is the most loaded machine at "
                    f"{summary.get('bottleneck_utilisation_pct')}% utilisation."
                )
            bullets.append(
                f"Average machine utilisation across the plan is "
                f"{summary.get('machine_utilisation_pct')}%."
            )
            recommendations.append(
                "Add capacity on the bottleneck machine or move eligible work to a "
                "less loaded machine to shorten the makespan."
            )
        elif "setup" in question or "changeover" in question:
            bullets.append(f"The plan contains {summary.get('setup_minutes')} minutes of setup.")
            recommendations.append(
                "Group work orders with the same setup family together to reduce changeovers."
            )
        elif "valid" in question or "constraint" in question:
            bullets.append(
                f"Independent validation: {validation.get('valid')} with "
                f"{validation.get('violation_count')} violations."
            )
            if validation.get("valid"):
                recommendations.append(
                    "The plan satisfies every hard constraint, so it can be published."
                )
        elif "compar" in question or "baseline" in question:
            comparison = evidence.get("comparison") or {}
            baseline = comparison.get("baseline_metrics") or {}
            optimised = comparison.get("optimised_metrics") or {}
            if baseline:
                bullets.append(
                    f"Baseline on-time delivery {baseline.get('on_time_delivery_pct')}% vs. "
                    f"optimised {optimised.get('on_time_delivery_pct')}%."
                )
                bullets.append(
                    f"Baseline weighted tardiness {baseline.get('weighted_tardiness')} vs. "
                    f"optimised {optimised.get('weighted_tardiness')}."
                )
            else:
                bullets.append("No baseline plan was supplied for this comparison.")
        else:
            bullets.append(f"Plan status: {summary.get('status')}.")
            bullets.append(
                f"Makespan {summary.get('makespan_hours')} hours, operating cost "
                f"{summary.get('operating_cost')}."
            )
            bullets.append(
                f"Setup time {summary.get('setup_minutes')} minutes, overtime "
                f"{summary.get('overtime_minutes')} minutes."
            )

        if late_orders and "late" not in question:
            bullets.append(
                f"{len(late_orders)} scheduled orders are late; the worst is "
                f"{max((o.get('tardiness_minutes') or 0) for o in late_orders)} minutes."
            )
        if not validation.get("valid", True):
            bullets.append(
                f"The validator reported {validation.get('violation_count')} constraint "
                "violations; this plan must not be published until they are resolved."
            )
        if not recommendations:
            recommendations.append(
                "Review the Gantt view to confirm the sequence on the bottleneck machine, "
                "then publish the plan when you are satisfied."
            )

        lines = [
            "[offline provider] Explanation generated without an external AI service.",
            headline,
        ]

        return CompletionResult(
            text=json.dumps(
                {
                    "summary": "\n".join(lines),
                    "explanation": headline,
                    "bullets": bullets,
                    "key_findings": bullets,
                    "recommendations": recommendations,
                    "evidence_refs": sorted(evidence.keys()),
                    "unsupported": False,
                }
            ),
            model="offline-deterministic",
            provider="mock",
            prompt_tokens=len(user_prompt) // 4,
            completion_tokens=len(bullets) * 8,
            latency_ms=1.0,
            raw={"offline": True},
        )


ADAPTERS: dict[str, ProviderAdapter] = {
    "openai": OpenAICompatibleAdapter(),
    "anthropic": AnthropicAdapter(),
    "openrouter": OpenRouterAdapter(),
    "custom": CustomAdapter(),
    "ollama": OllamaAdapter(),
    "mock": MockAdapter(),
}


def get_adapter(kind: str) -> ProviderAdapter:
    adapter = ADAPTERS.get(kind)
    if adapter is None:
        raise ProviderError(
            f"Unsupported provider kind '{kind}'. Supported: {', '.join(PROVIDER_KINDS)}."
        )
    return adapter


def provider_catalog() -> list[dict[str, Any]]:
    """Static catalogue used by the AI settings screen."""
    catalog = []
    for kind in PROVIDER_KINDS:
        defaults = PROVIDER_DEFAULTS[kind]
        catalog.append(
            {
                "kind": kind,
                "label": PROVIDER_LABELS[kind],
                "default_base_url": defaults["base_url"],
                "default_model": defaults["model"],
                "suggested_models": defaults["models"],
                "requires_key": defaults["requires_key"],
                "hosted": defaults["hosted"],
                "supports_model_discovery": kind != "mock",
            }
        )
    return catalog
