"""Rate limiting through the HTTP API, with production-like limits."""

CONTEXT_PROPERTIES = {"ratelimit.session-limit": 5, "ratelimit.ip-limit": 60}


def test_schedule_rate_limiting_endpoint(client):
    headers = {"X-Session-ID": "test-rate-limit-session", "X-Forwarded-For": "192.0.2.100"}
    for _ in range(5):
        client.post("/api/schedule", json={"interests": "Rate limited topic"}, headers=headers)

    response = client.post("/api/schedule", json={"interests": "Rate limited topic"}, headers=headers)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    body = response.json()
    assert body["valid"] is False
    assert "Rate limit exceeded" in body["validationMessage"]

    # A different session behind the same IP is still allowed
    other = client.post(
        "/api/schedule", json={"interests": "Rate limited topic"},
        headers={"X-Session-ID": "another-session", "X-Forwarded-For": "192.0.2.100"},
    )
    assert other.status_code == 200


def test_alternatives_rate_limiting(client):
    headers = {"X-Session-ID": "alt-session", "X-Forwarded-For": "192.0.2.101"}
    for _ in range(5):
        client.post("/api/schedule/alternatives", json={"talkId": 7006}, headers=headers)
    response = client.post("/api/schedule/alternatives", json={"talkId": 7006}, headers=headers)
    assert response.status_code == 429
    assert "Rate limit exceeded" in response.json()["message"]
