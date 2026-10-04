"""Swapping the backend: the OpenAI-compatible client against a mock Chat Completions API."""

from dvxaisched.llm import LlmRequest
from dvxaisched.openai_compat import build_chat_completions_body, parse_chat_completions_response
from sse import assert_conflict_free

PORT = 18763
CONTEXT_PROPERTIES = {
    "llm.provider": "openai",
    "llm.openai.model": "local-test-model",
    "llm.openai.api-key": "sk-test",
    "mock-llm-api.enabled": "true",
    "micronaut.server.port": PORT,
    "llm.openai.base-url": f"http://localhost:{PORT}/mock-api/openai/v1",
}


def test_request_body_uses_json_schema_response_format():
    body = build_chat_completions_body(LlmRequest("plan-day", "sys", "usr", {"type": "object"}), "m", 0.1)
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]
    assert body["response_format"] == {"type": "json_schema", "json_schema": {"name": "plan_day", "schema": {"type": "object"}}}


def test_response_parsing_tolerates_markdown_fences():
    assert parse_chat_completions_response('{"choices": [{"message": {"content": "```json\\n{\\"a\\": 2}\\n```"}}]}') == {"a": 2}


def test_health_reports_openai_model(client):
    assert client.get("/api/health").json()["model"] == "local-test-model"


def test_schedule_through_openai_compatible_client(client):
    body = client.post("/api/schedule", json={"interests": "Cloud native Java"}).json()
    assert body["valid"] is True
    assert_conflict_free(body)

    recorded = client.get("/mock-api/requests").json()
    assert {r["api"] for r in recorded} == {"openai"}, "Gemini must not be called when the provider is swapped"
    assert all(r["authorization"] == "Bearer sk-test" for r in recorded)
    assert all(r["body"]["model"] == "local-test-model" for r in recorded)
