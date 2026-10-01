"""Curator verification must exercise the saved model, without live API calls."""
from unittest.mock import MagicMock, patch

import pytest

from collection_web import integrations
from collection_web.store import DomainError


def test_connection_generates_with_exact_saved_model():
    settings = {"llm_url": "https://example.test/v1", "llm_model": "gpt-6-luna", "llm_key": ""}
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": '{"ok":true}'}}]}
    with patch.object(integrations, "http", return_value=response) as request:
        message = integrations.test_connection(settings, "llm")
    assert "gpt-6-luna" in message
    assert request.call_args.args == ("POST", "https://example.test/v1/chat/completions")
    assert request.call_args.kwargs["json"]["model"] == "gpt-6-luna"
    assert request.call_args.kwargs["json"]["max_completion_tokens"] == 1024


def test_connection_rejects_missing_model_before_request():
    with patch.object(integrations, "http") as request:
        with pytest.raises(DomainError, match="model"):
            integrations.test_connection({"llm_url": "https://example.test/v1", "llm_model": ""}, "llm")
    request.assert_not_called()


def test_connection_rejects_unreadable_generation():
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": "not JSON"}}]}
    with patch.object(integrations, "http", return_value=response):
        with pytest.raises(DomainError, match="unreadable"):
            integrations.test_connection({"llm_url": "https://example.test/v1", "llm_model": "custom"}, "llm")


def test_connection_does_not_fallback_when_model_is_unavailable():
    with patch.object(integrations, "http", side_effect=DomainError("Model unavailable")) as request:
        with pytest.raises(DomainError, match="Model unavailable"):
            integrations.test_connection({"llm_url": "https://example.test/v1", "llm_model": "gpt-6-luna"}, "llm")
    assert request.call_count == 1


def test_custom_provider_keeps_its_model_and_generation_parameters():
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": '{"ok":true}'}}]}
    with patch.object(integrations, "http", return_value=response) as request:
        integrations.call_llm({"llm_url": "http://local.test/v1", "llm_model": "local-model"}, "test")
    assert request.call_args.kwargs["json"]["model"] == "local-model"
    assert "reasoning_effort" not in request.call_args.kwargs["json"]
    assert "max_completion_tokens" not in request.call_args.kwargs["json"]


def test_saved_luna_survives_restart_and_test_reports_model(tmp_path):
    from collection_web.app import create_app
    from collection_web.store import Store

    app = create_app(tmp_path, password="test-password")
    client = app.test_client()
    headers = {"X-CSRF-Token": client.post("/api/login", json={"password": "test-password"}).json["csrf"]}
    result = client.post("/api/settings", headers=headers, json={
        "llm_url": "https://example.test/v1", "llm_model": "gpt-6-luna", "llm_key": "test-only"})
    assert result.status_code == 200
    try:
        with patch.object(integrations, "call_llm", return_value={"ok": True}) as generate:
            assert client.post("/api/connections/test", headers=headers, json={"service": "llm"}).status_code == 202
            app.extensions["collection_service"].close()
        assert generate.call_args.args[0]["llm_model"] == "gpt-6-luna"
        saved = Store(tmp_path).read()
        assert saved["settings"]["llm_model"] == "gpt-6-luna"
        assert saved["jobs"][0]["status"] == "succeeded"
        assert "gpt-6-luna responded successfully" in saved["jobs"][0]["message"]
    finally:
        app.extensions["collection_service"].close()
