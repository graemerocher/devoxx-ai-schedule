"""Gemini API errors are handled by the workflow's fail-closed guardrail."""

PORT = 18762
CONTEXT_PROPERTIES = {
    "llm.provider": "gemini",
    "llm.gemini.api-key": "wrong-key",
    "mock-llm-api.enabled": "true",
    "micronaut.server.port": PORT,
    "llm.gemini.base-url": f"http://localhost:{PORT}/mock-api/gemini",
}


def test_unauthorized_gemini_call_fails_closed(client):
    body = client.post("/api/schedule", json={"interests": "Java"}).json()
    assert body["valid"] is False
    assert "temporarily unavailable" in body["validationMessage"]
