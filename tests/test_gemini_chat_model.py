"""The Micronaut LangChain4j Gemini ChatModel, exercised against a mock Gemini API."""

from sse import assert_conflict_free

PORT = 18761
CONTEXT_PROPERTIES = {
    "langchain4j.google-ai-gemini.enabled": "true",
    "langchain4j.google-ai-gemini.api-key": "test-gemini-key",
    "langchain4j.google-ai-gemini.model-name": "gemini-test-model",
    "langchain4j.google-ai-gemini.chat-model.base-url": f"http://localhost:{PORT}/mock-gemini/v1beta",
    "mock-gemini.enabled": "true",
    "micronaut.server.port": PORT,
}


def test_health_reports_gemini_model(client):
    assert client.get("/api/health").json()["model"] == "gemini-test-model"


def test_schedule_through_gemini(client):
    body = client.post("/api/schedule", json={"interests": "Java and AI agents"}).json()
    assert body["valid"] is True, body
    assert all(d["talks"] for d in body["days"])
    assert_conflict_free(body)

    recorded = client.get("/mock-gemini/requests").json()
    assert [r["task"] for r in recorded].count("plan-day") == 5
    assert {r["path"] for r in recorded} == {"gemini-test-model:generateContent"}
    assert all(r["apiKey"] == "test-gemini-key" for r in recorded)
    assert all(r["generationConfig"].get("responseMimeType") == "application/json" for r in recorded)
