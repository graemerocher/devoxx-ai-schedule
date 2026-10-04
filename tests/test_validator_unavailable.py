"""The guardrail fails closed when the validator cannot be reached."""

CONTEXT_PROPERTIES = {"fake-llm.fail-tasks": "validate-interests"}


def test_validator_failure_rejects_request(client):
    body = client.post("/api/schedule", json={"interests": "Java"}).json()
    assert body["valid"] is False
    assert "temporarily unavailable" in body["validationMessage"]
    assert "plan-day" not in client.get("/test/fake-llm/stats").json()["calls"]
