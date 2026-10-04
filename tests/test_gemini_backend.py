"""The real Gemini client, exercised against a mock Generative Language API."""

import json

from dvxaisched.gemini import build_generate_content_body, parse_generate_content_response
from dvxaisched.llm import LlmException, LlmRequest
from sse import assert_conflict_free

PORT = 18761
CONTEXT_PROPERTIES = {
    "llm.provider": "gemini",
    "llm.gemini.model": "gemini-test-model",
    "mock-llm-api.enabled": "true",
    "micronaut.server.port": PORT,
    "llm.gemini.base-url": f"http://localhost:{PORT}/mock-api/gemini",
}


def test_request_body_uses_structured_output():
    body = build_generate_content_body(LlmRequest("t", "sys", "usr", {"type": "object"}), 0.2)
    assert body["systemInstruction"]["parts"][0]["text"] == "sys"
    assert body["contents"] == [{"role": "user", "parts": [{"text": "usr"}]}]
    assert body["generationConfig"] == {
        "temperature": 0.2, "responseMimeType": "application/json", "responseJsonSchema": {"type": "object"},
    }


def test_response_parsing():
    payload = {"candidates": [{"content": {"parts": [{"text": "thinking", "thought": True}, {"text": '{"a": 1}'}]}}]}
    assert parse_generate_content_response(json.dumps(payload)) == {"a": 1}
    for bad in ({"promptFeedback": {"blockReason": "SAFETY"}}, {"candidates": [{"content": {"parts": [{"text": "nope"}]}}]}):
        try:
            parse_generate_content_response(json.dumps(bad))
            raise AssertionError("expected LlmException")
        except LlmException:
            pass


def test_health_reports_gemini_model(client):
    assert client.get("/api/health").json()["model"] == "gemini-test-model"


def test_schedule_through_gemini_client(client):
    body = client.post("/api/schedule", json={"interests": "Java and AI agents"}).json()
    assert body["valid"] is True
    assert all(d["talks"] for d in body["days"])
    assert_conflict_free(body)

    recorded = [r for r in client.get("/mock-api/requests").json() if r["api"] == "gemini"]
    assert len(recorded) == 6, "One validation call and five concurrent day planner calls"
    assert {r["path"] for r in recorded} == {"gemini-test-model:generateContent"}
    assert all(r["apiKey"] == "test-gemini-key" for r in recorded)
    assert all(r["body"]["generationConfig"]["responseMimeType"] == "application/json" for r in recorded)
