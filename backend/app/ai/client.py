"""AI client: secure key handling, budget control and grounded explanations."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import providers as provider_module
from app.ai.grounding import (
    SYSTEM_PROMPT,
    EvidencePack,
    VerificationResult,
    build_prompt,
    parse_answer,
    verify_answer,
)
from app.ai.providers import (
    CompletionResult,
    ConnectionTest,
    ProviderConfig,
    ProviderError,
    get_adapter,
)
from app.config import settings
from app.models import AIProvider, AISettings

#: Indicative public list prices (USD per 1k tokens) used for *estimates* only.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4.1-mini": (0.0004, 0.0016),
    "o4-mini": (0.0011, 0.0044),
    "claude-3-5-haiku-latest": (0.0008, 0.004),
    "claude-3-5-sonnet-latest": (0.003, 0.015),
    "claude-3-7-sonnet-latest": (0.003, 0.015),
    "llama3.1": (0.0, 0.0),
    "llama3.2": (0.0, 0.0),
    "mistral": (0.0, 0.0),
    "qwen2.5": (0.0, 0.0),
}


class AIError(RuntimeError):
    """Raised when an AI request cannot be served."""


# ---------------------------------------------------------------------------
# Secret handling
# ---------------------------------------------------------------------------


_SECRET_AAD = b"intelligent-production-scheduling:ai-provider-key:v1"


def _encryption_key() -> bytes:
    """Derive a 256-bit, domain-separated encryption key from SECRET_KEY."""
    return hashlib.sha256(
        b"ips-ai-key-encryption-v1\0" + settings.secret_key.encode("utf-8")
    ).digest()


def encrypt_secret(plaintext: str) -> str:
    """AES-256-GCM encrypt a provider credential for database storage."""
    if not plaintext:
        return ""
    nonce = os.urandom(12)
    ciphertext_and_tag = AESGCM(_encryption_key()).encrypt(
        nonce, plaintext.encode("utf-8"), _SECRET_AAD
    )
    payload = b"v1" + nonce + ciphertext_and_tag
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decrypt_secret(payload: str | None) -> str | None:
    """Authenticate and decrypt a version-1 encrypted API key.

    Invalid payloads and authentication failures return ``None`` rather than
    exposing the credential or an internal traceback to the caller.
    """
    if not payload:
        return None
    try:
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        if len(raw) < 2 + 12 + 16 or raw[:2] != b"v1":
            return None
        plaintext = AESGCM(_encryption_key()).decrypt(raw[2:14], raw[14:], _SECRET_AAD)
        return plaintext.decode("utf-8")
    except (InvalidTag, ValueError, TypeError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Settings / provider selection
# ---------------------------------------------------------------------------


def get_ai_settings(session: Session) -> AISettings:
    row = session.get(AISettings, 1)
    if row is None:
        row = AISettings(id=1)
        session.add(row)
        session.flush()
    return row


def provider_to_config(provider: AIProvider) -> ProviderConfig:
    return ProviderConfig(
        kind=provider.kind,
        model=provider.model,
        base_url=provider.base_url,
        api_key=decrypt_secret(provider.api_key_encrypted),
        timeout_seconds=provider.request_timeout_seconds or settings.ai_request_timeout_seconds,
        max_tokens=provider.max_tokens or settings.ai_max_tokens,
        temperature=provider.temperature,
        max_retries=provider.max_retries,
    )


def select_provider(session: Session, provider_id: int | None = None) -> AIProvider:
    providers = list(session.scalars(select(AIProvider).where(AIProvider.is_enabled.is_(True))))
    if not providers:
        raise AIError(
            "No AI provider is configured. Add one in AI settings, or keep AI disabled — "
            "scheduling works without any AI provider."
        )
    if provider_id is not None:
        for provider in providers:
            if provider.id == provider_id:
                return provider
        raise AIError(f"AI provider {provider_id} is not available.")
    for provider in providers:
        if provider.is_default:
            return provider
    return providers[0]


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate the USD cost of a call (0 for local providers)."""
    entry = PRICING.get(model)
    if entry is None:
        for key, value in PRICING.items():
            if key in model:
                entry = value
                break
    if entry is None:
        return 0.0
    return round((prompt_tokens / 1000.0) * entry[0] + (completion_tokens / 1000.0) * entry[1], 6)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass
class ExplanationResult:
    answer: dict[str, Any]
    verification: VerificationResult
    evidence: EvidencePack
    provider: str
    model: str
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "verification": self.verification.to_dict(),
            "evidence": self.evidence.to_dict(),
            "provider": self.provider,
            "model": self.model,
            "latency_ms": round(self.latency_ms, 1),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
        }


class AIClient:
    """Backend-only entry point for every language-model interaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # -- provider management ---------------------------------------------
    def catalog(self) -> list[dict[str, Any]]:
        return provider_module.provider_catalog()

    def test_connection(self, provider: AIProvider) -> ConnectionTest:
        adapter = get_adapter(provider.kind)
        config = provider_to_config(provider)
        result = adapter.test_connection(config)
        provider.last_test_at = _now()
        provider.last_test_status = "ok" if result.ok else "error"
        provider.last_test_message = result.message
        self.session.flush()
        return result

    def list_models(self, provider: AIProvider) -> list[str]:
        adapter = get_adapter(provider.kind)
        return adapter.list_models(provider_to_config(provider))

    # -- explanations -----------------------------------------------------
    def explain(
        self,
        provider: AIProvider,
        evidence: EvidencePack,
        *,
        max_retries: int | None = None,
    ) -> ExplanationResult:
        ai_settings = get_ai_settings(self.session)
        if not ai_settings.enabled:
            raise AIError("AI features are disabled in the platform settings.")

        config = provider_to_config(provider)
        adapter = get_adapter(config.kind)
        prompt = build_prompt(evidence)
        attempts = (max_retries if max_retries is not None else config.max_retries) + 1

        last_error: Exception | None = None
        completion: CompletionResult | None = None
        for attempt in range(attempts):
            try:
                completion = adapter.complete(config, SYSTEM_PROMPT, prompt)
                break
            except ProviderError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(min(2**attempt, 4))

        if completion is None:
            raise AIError(str(last_error) if last_error else "The AI provider failed.")

        answer = parse_answer(completion.text)
        verification = verify_answer(json.dumps(answer, default=str), evidence)
        answer.setdefault("unsupported", not verification.ok)

        cost = estimate_cost(
            completion.model, completion.prompt_tokens, completion.completion_tokens
        )
        ai_settings.spent_usd = round(ai_settings.spent_usd + cost, 6)
        self.session.flush()

        return ExplanationResult(
            answer=answer,
            verification=verification,
            evidence=evidence,
            provider=provider.kind,
            model=completion.model,
            latency_ms=completion.latency_ms,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            estimated_cost_usd=cost,
        )

    def check_budget(self, provider: AIProvider) -> tuple[bool, str]:
        ai_settings = get_ai_settings(self.session)
        if ai_settings.spent_usd >= ai_settings.spend_limit_usd:
            return False, (
                f"The monthly AI spend limit of ${ai_settings.spend_limit_usd:.2f} has been "
                f"reached (${ai_settings.spent_usd:.2f} used)."
            )
        if (
            provider.kind in {"openai", "anthropic", "openrouter"}
            and not ai_settings.allow_paid_providers
        ):
            return False, "Paid AI providers are disabled in the platform settings."
        if provider.kind in {"ollama", "custom"} and not ai_settings.allow_local_providers:
            return False, "Local / custom AI providers are disabled in the platform settings."
        return True, ""


def _now():
    from datetime import datetime

    return datetime.now(UTC)
