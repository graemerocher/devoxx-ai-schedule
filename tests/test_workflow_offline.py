"""Behaviour when every LLM call fails."""

CONTEXT_PROPERTIES = {"fake-llm.fail-tasks": "plan-day,build-schedule,recommend-alternatives"}


def test_deterministic_catalog_fallback(client):
    body = client.post("/api/schedule", json={"interests": "Java and cloud"}).json()
    assert body["valid"] is True
    assert body["overview"].startswith("Personalized schedule curated")
    talks = [t for d in body["days"] for t in d["talks"]]
    assert talks
    assert all(t["reason"] for t in talks)


def test_alternatives_fall_back_to_deterministic_ranking(client):
    talks = client.get("/api/talks", params={"day": "wednesday", "limit": 100}).json()
    target = next(t for t in talks if t["startTime"] == "14:00" and t["endTime"] == "14:50")
    body = client.post("/api/schedule/alternatives", json={"interests": "Java", "talkId": target["id"]}).json()
    assert body["hasAlternatives"] is True
    assert len(body["alternatives"]) == 3
    assert all("high community interest" in a["reason"] for a in body["alternatives"])
