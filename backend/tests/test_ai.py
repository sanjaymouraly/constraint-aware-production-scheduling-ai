"""Tests for the AI subsystem.

The AI layer must never be able to influence the schedule.  These tests
verify that the adapters behave correctly (including timeouts and invalid
credentials), that secrets are encrypted at rest, that the budget guard
works and that every numeric claim in an answer is verified against the
grounded evidence pack.
"""

from __future__ import annotations

import json

import pytest
from app.ai.client import AIClient, decrypt_secret, encrypt_secret, estimate_cost, select_provider
from app.ai.grounding import (
    EvidencePack,
    build_evidence,
    build_prompt,
    normalize_answer,
    parse_answer,
    verify_answer,
)
from app.ai.providers import (
    MockAdapter,
    ProviderConfig,
    ProviderError,
    get_adapter,
    provider_catalog,
)
from app.models import AIProvider
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


class TestAnswerNormalisation:
    """The model output is always normalised to the documented schema.

    Small local models (for example a 1B model on Ollama) regularly answer
    with a different JSON shape.  The platform must still render a useful
    answer and must be transparent that the answer was off-schema.
    """

    def test_conforming_answer_is_preserved(self) -> None:
        answer = parse_answer(
            json.dumps(
                {
                    "summary": "Three orders are late.",
                    "bullets": ["WO-1 is 2 h late."],
                    "recommendations": ["Add a shift on PRESS-01."],
                    "evidence_refs": ["summary.orders"],
                    "unsupported": False,
                }
            )
        )
        assert answer["summary"] == "Three orders are late."
        assert answer["bullets"] == ["WO-1 is 2 h late."]
        assert answer["recommendations"] == ["Add a shift on PRESS-01."]
        assert answer["evidence_refs"] == ["summary.orders"]
        assert answer["unsupported"] is False
        assert "raw_answer" not in answer

    def test_off_schema_answer_is_still_renderable(self) -> None:
        answer = parse_answer(json.dumps({"machine_code": "PRESS-01", "utilisation_pct": 7.21}))
        assert answer["summary"]
        assert "PRESS-01" in answer["summary"]
        assert answer["bullets"] == []
        assert answer["recommendations"] == []
        assert answer["evidence_refs"] == []
        # An answer that ignored the agreed schema is not trustworthy.
        assert answer["unsupported"] is True
        assert answer["raw_answer"]["machine_code"] == "PRESS-01"

    def test_alternative_key_names_are_accepted(self) -> None:
        answer = normalize_answer(
            {
                "explanation": "The plan is valid.",
                "key_findings": ["No violations."],
                "actions": ["Publish the plan."],
            }
        )
        assert answer["summary"] == "The plan is valid."
        assert answer["bullets"] == ["No violations."]
        assert answer["recommendations"] == ["Publish the plan."]

    def test_wrapped_answer_is_unwrapped(self) -> None:
        """A model that wraps its answer in one key is still understood."""
        answer = normalize_answer(
            {
                "analysis": {
                    "reasoning": "Three orders are late because of a material shortage.",
                    "recommendations": ["Expedite the receipt."],
                }
            }
        )
        assert answer["summary"] == ("Three orders are late because of a material shortage.")
        assert answer["recommendations"] == ["Expedite the receipt."]
        assert answer["unsupported"] is False

    def test_malformed_json_is_reported_as_unsupported(self) -> None:
        answer = parse_answer("this is not json at all")
        assert answer["summary"] == "this is not json at all"
        assert answer["unsupported"] is True

    def test_empty_answer_is_reported(self) -> None:
        answer = parse_answer("")
        assert answer["unsupported"] is True
        assert "empty" in answer["summary"].lower()


class TestSecretHandling:
    def test_encrypt_decrypt_round_trip(self) -> None:
        secret = "sk-super-secret-value"
        encrypted = encrypt_secret(secret)
        assert encrypted != secret
        assert secret not in encrypted
        assert decrypt_secret(encrypted) == secret

    def test_encrypt_is_not_deterministic_but_always_decrypts(self) -> None:
        first = encrypt_secret("same")
        second = encrypt_secret("same")
        assert first != second  # random nonce / IV
        assert decrypt_secret(first) == decrypt_secret(second) == "same"

    def test_decrypt_of_none_is_none(self) -> None:
        assert decrypt_secret(None) is None

    def test_tampered_ciphertext_is_rejected(self) -> None:
        import base64

        encoded = encrypt_secret("credential")
        raw = bytearray(base64.urlsafe_b64decode(encoded.encode("ascii")))
        raw[-1] ^= 0x01
        tampered = base64.urlsafe_b64encode(raw).decode("ascii")
        assert decrypt_secret(tampered) is None

    def test_api_never_returns_the_api_key(self, client: TestClient, auth: dict) -> None:
        response = client.post(
            "/api/ai/providers",
            json={
                "name": "Secret provider",
                "kind": "openai",
                "model": "gpt-4o-mini",
                "api_key": "sk-do-not-leak-me",
            },
            headers=auth,
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert "api_key" not in body
        assert body["has_api_key"] is True
        assert "do-not-leak-me" not in json.dumps(body)
        assert body["masked_api_key"].endswith("me")

    def test_stored_key_is_encrypted_in_the_database(
        self, client: TestClient, auth: dict, session: Session
    ) -> None:
        client.post(
            "/api/ai/providers",
            json={
                "name": "Encrypted provider",
                "kind": "openai",
                "model": "gpt-4o-mini",
                "api_key": "sk-plaintext-should-not-be-stored",
            },
            headers=auth,
        )
        session.expire_all()
        row = session.query(AIProvider).filter_by(name="Encrypted provider").one()
        assert "sk-plaintext-should-not-be-stored" not in (row.api_key_encrypted or "")
        assert decrypt_secret(row.api_key_encrypted) == "sk-plaintext-should-not-be-stored"


class TestProviderAdapters:
    def test_catalog_covers_every_documented_provider(self) -> None:
        kinds = {entry["kind"] for entry in provider_catalog()}
        assert kinds == {"openai", "anthropic", "openrouter", "custom", "ollama", "mock"}

    def test_every_catalog_entry_is_complete(self) -> None:
        for entry in provider_catalog():
            assert entry["label"]
            assert "default_model" in entry
            assert "requires_key" in entry
            assert "supports_model_discovery" in entry
            assert "hosted" in entry

    def test_get_adapter_returns_an_adapter_for_every_kind(self) -> None:
        for entry in provider_catalog():
            adapter = get_adapter(entry["kind"])
            assert hasattr(adapter, "complete")
            assert hasattr(adapter, "test_connection")
            assert hasattr(adapter, "list_models")

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ProviderError):
            get_adapter("does-not-exist")

    def test_mock_adapter_is_deterministic(self) -> None:
        adapter = MockAdapter()
        config = ProviderConfig(kind="mock", model="mock-grounded")
        prompt = build_prompt(
            EvidencePack(
                summary={"on_time_delivery_pct": 95.0, "operations_scheduled": 10},
                question="How is the plan?",
            )
        )
        first = adapter.complete(config, "system", prompt)
        second = adapter.complete(config, "system", prompt)
        assert first.text == second.text
        assert json.loads(first.text)["summary"]

    def test_mock_adapter_reports_its_offline_status(self) -> None:
        adapter = MockAdapter()
        config = ProviderConfig(kind="mock", model="mock-grounded")
        result = adapter.test_connection(config)
        assert result.ok
        assert "offline" in result.message.lower()

    def test_openai_adapter_times_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A hanging endpoint must raise a provider error, not hang forever."""
        import app.ai.providers as providers

        def _raise(*args, **kwargs):
            raise providers.httpx.TimeoutException("timed out")

        monkeypatch.setattr(providers.httpx.Client, "post", _raise)
        adapter = get_adapter("openai")
        config = ProviderConfig(
            kind="openai",
            model="gpt-4o-mini",
            base_url="https://api.openai.com/v1",
            api_key="sk-invalid",
            timeout_seconds=1.0,
        )
        with pytest.raises(ProviderError):
            adapter.complete(config, "system", "hello")

    def test_anthropic_adapter_reports_an_invalid_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.ai.providers as providers

        class _Response:
            status_code = 401
            text = '{"error": {"message": "invalid x-api-key"}}'

            def raise_for_status(self):
                raise providers.httpx.HTTPStatusError("401", request=None, response=self)

            def json(self):
                return {"error": {"message": "invalid x-api-key"}}

        monkeypatch.setattr(providers.httpx.Client, "post", lambda *a, **k: _Response())
        adapter = get_adapter("anthropic")
        config = ProviderConfig(
            kind="anthropic",
            model="claude-3-5-haiku-latest",
            base_url="https://api.anthropic.com",
            api_key="invalid",
        )
        with pytest.raises(ProviderError) as excinfo:
            adapter.complete(config, "system", "hello")
        assert "401" in str(excinfo.value) or "invalid" in str(excinfo.value).lower()

    @staticmethod
    def _fake_response(payload: dict) -> object:
        class _Response:
            status_code = 200
            text = json.dumps(payload)

            def raise_for_status(self):
                return None

            def json(self):
                return payload

        return _Response()

    def test_truncated_openai_answer_reports_the_token_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A model that runs out of output tokens must say so, not return nothing."""
        import app.ai.providers as providers

        payload = {
            "model": "qwen3.5:4b",
            "choices": [
                {
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 1500},
        }
        monkeypatch.setattr(
            providers.httpx.Client, "post", lambda *a, **k: self._fake_response(payload)
        )
        adapter = get_adapter("ollama")
        config = ProviderConfig(
            kind="ollama",
            model="qwen3.5:4b",
            base_url="http://127.0.0.1:11434/v1",
            max_tokens=1500,
        )
        with pytest.raises(ProviderError) as excinfo:
            adapter.complete(config, "system", "hello")
        assert "1500" in str(excinfo.value)
        assert "max tokens" in str(excinfo.value).lower()

    def test_empty_openai_answer_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.ai.providers as providers

        payload = {
            "model": "gpt-4o-mini",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "   "},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 0},
        }
        monkeypatch.setattr(
            providers.httpx.Client, "post", lambda *a, **k: self._fake_response(payload)
        )
        adapter = get_adapter("openai")
        config = ProviderConfig(
            kind="openai",
            model="gpt-4o-mini",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
        )
        with pytest.raises(ProviderError) as excinfo:
            adapter.complete(config, "system", "hello")
        assert "empty" in str(excinfo.value).lower()

    def test_truncated_anthropic_answer_reports_the_token_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.ai.providers as providers

        payload = {
            "model": "claude-3-5-haiku-latest",
            "content": [],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 50, "output_tokens": 800},
        }
        monkeypatch.setattr(
            providers.httpx.Client, "post", lambda *a, **k: self._fake_response(payload)
        )
        adapter = get_adapter("anthropic")
        config = ProviderConfig(
            kind="anthropic",
            model="claude-3-5-haiku-latest",
            base_url="https://api.anthropic.com/v1",
            api_key="sk-ant-test",
            max_tokens=800,
        )
        with pytest.raises(ProviderError) as excinfo:
            adapter.complete(config, "system", "hello")
        assert "800" in str(excinfo.value)


class TestBudgetGuard:
    def test_spend_limit_blocks_further_calls(self, session: Session) -> None:
        from app.ai.client import get_ai_settings

        settings = get_ai_settings(session)
        settings.spent_usd = settings.spend_limit_usd
        session.flush()
        provider = AIProvider(name="Budget", kind="mock", model="mock-grounded")
        session.add(provider)
        session.flush()
        client = AIClient(session)
        allowed, reason = client.check_budget(provider)
        assert not allowed
        assert "spend limit" in reason.lower()

    def test_paid_providers_can_be_disabled(self, session: Session) -> None:
        from app.ai.client import get_ai_settings

        settings = get_ai_settings(session)
        settings.allow_paid_providers = False
        session.flush()
        provider = AIProvider(name="Paid", kind="openai", model="gpt-4o-mini")
        session.add(provider)
        session.flush()
        allowed, reason = AIClient(session).check_budget(provider)
        assert not allowed
        assert "paid" in reason.lower()

    def test_local_providers_can_be_disabled(self, session: Session) -> None:
        from app.ai.client import get_ai_settings

        settings = get_ai_settings(session)
        settings.allow_local_providers = False
        session.flush()
        provider = AIProvider(name="Local", kind="ollama", model="llama3.1")
        session.add(provider)
        session.flush()
        allowed, reason = AIClient(session).check_budget(provider)
        assert not allowed
        assert "local" in reason.lower()

    def test_cost_estimate_is_zero_for_local_models(self) -> None:
        assert estimate_cost("llama3.1", 1000, 1000) == 0.0
        assert estimate_cost("gpt-4o-mini", 1000, 1000) > 0.0

    def test_no_provider_raises_a_clear_error(self, session: Session) -> None:
        for row in session.query(AIProvider).all():
            row.is_enabled = False
        session.flush()
        with pytest.raises(Exception) as excinfo:
            select_provider(session)
        assert "no ai provider" in str(excinfo.value).lower()


class TestGrounding:
    def test_evidence_pack_contains_only_solver_facts(self, instance) -> None:
        from app.scheduling.fcfs import run_fcfs
        from app.scheduling.postprocess import apply_setup_gaps
        from app.scheduling.validator import validate_schedule

        assignments = run_fcfs(instance).assignments
        apply_setup_gaps(instance, assignments)
        validation = validate_schedule(instance, assignments)
        from app.scheduling.metrics import compute_metrics

        metrics = compute_metrics(instance, assignments)
        evidence = build_evidence(instance, assignments, validation, metrics=metrics)
        payload = evidence.to_dict()
        assert payload["summary"]["operations_scheduled"] == len(instance.tasks)
        # The evidence lists *work orders* (not operations) with their lateness.
        assert payload["orders"]
        assert all("work_order_code" in row for row in payload["orders"])
        assert payload["validation"]["valid"] is True

    def test_verify_answer_accepts_grounded_numbers(self) -> None:
        evidence = EvidencePack(summary={"on_time_delivery_pct": 95.0})
        result = verify_answer("The plan reaches 95.0% on-time delivery.", evidence)
        assert result.ok
        assert not result.unsupported_claims

    def test_verify_answer_flags_invented_numbers(self) -> None:
        evidence = EvidencePack(summary={"on_time_delivery_pct": 95.0})
        result = verify_answer("The plan reaches 99.7% on-time delivery.", evidence)
        assert not result.ok
        assert result.unsupported_claims

    def test_prompt_contains_the_evidence(self) -> None:
        evidence = EvidencePack(summary={"on_time_delivery_pct": 95.0}, question="Why?")
        prompt = build_prompt(evidence)
        assert "95.0" in prompt
        assert "Why?" in prompt


class TestExplainEndpoint:
    def test_explain_requires_authentication(self, client: TestClient) -> None:
        assert (
            client.post("/api/ai/explain", json={"schedule_id": 1, "question": "why"}).status_code
            == 401
        )

    def test_explain_with_the_offline_provider(self, client: TestClient, auth: dict) -> None:
        run = client.post("/api/scheduling/run", json={"engine": "fcfs"}, headers=auth)
        schedule_id = run.json()["schedule"]["id"]
        response = client.post(
            "/api/ai/explain",
            json={"schedule_id": schedule_id, "question": "How is this plan?"},
            headers=auth,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["answer"]["summary"]
        assert body["verification"]["ok"] is True
        assert body["provider"] == "mock"
        assert body["evidence"]["summary"]["operations_scheduled"] == 94

    def test_explain_rejects_an_unknown_schedule(self, client: TestClient, auth: dict) -> None:
        response = client.post(
            "/api/ai/explain", json={"schedule_id": 999999, "question": "Why?"}, headers=auth
        )
        assert response.status_code == 404

    def test_explain_requires_a_question(self, client: TestClient, auth: dict) -> None:
        response = client.post(
            "/api/ai/explain", json={"schedule_id": 1, "question": ""}, headers=auth
        )
        assert response.status_code == 422

    def test_ai_can_be_disabled_globally(self, client: TestClient, auth: dict) -> None:
        settings = client.get("/api/ai/settings", headers=auth).json()
        client.put("/api/ai/settings", json={**settings, "enabled": False}, headers=auth)
        run = client.post("/api/scheduling/run", json={"engine": "fcfs"}, headers=auth)
        schedule_id = run.json()["schedule"]["id"]
        response = client.post(
            "/api/ai/explain",
            json={"schedule_id": schedule_id, "question": "Why?"},
            headers=auth,
        )
        assert response.status_code == 502
        assert "disabled" in response.json()["detail"].lower()
        # re-enable for the remaining tests
        client.put("/api/ai/settings", json={**settings, "enabled": True}, headers=auth)

    def test_scheduling_works_without_any_ai_provider(
        self, client: TestClient, auth: dict, session: Session
    ) -> None:
        """The scheduler must never depend on the AI layer."""
        for row in session.query(AIProvider).all():
            row.is_enabled = False
        session.commit()
        response = client.post("/api/scheduling/run", json={"engine": "fcfs"}, headers=auth)
        assert response.status_code == 200
        assert response.json()["validation"]["valid"] is True
