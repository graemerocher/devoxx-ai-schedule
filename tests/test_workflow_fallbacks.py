"""When the parallel day planners fail, the tool-equipped monolithic agent takes over."""

from sse import assert_conflict_free, sse_events

CONTEXT_PROPERTIES = {"fake-llm.fail-tasks": "plan-day"}


def test_monolithic_fallback(client):
    body = client.post("/api/schedule", json={"interests": "Security and supply chain"}).json()
    assert body["valid"] is True
    assert body["theme"] == "Fake monolithic schedule"
    assert "from 6 tool results" in body["overview"]
    assert any(d["talks"] for d in body["days"])
    assert_conflict_free(body)

    stats = client.get("/test/fake-llm/stats").json()
    assert stats["calls"]["plan-day"] >= 5, "Every day worker is attempted (and retried) before falling back"
    assert stats["calls"]["build-schedule"] == 2, "One tool-calling round trip, then the final answer"
    assert stats["toolsCalled"] == ["get_conference_tracks"] + ["get_talks_for_day"] * 5


def test_tool_calls_are_streamed(client):
    events = sse_events(client.get("/api/schedule/stream", params={"interests": "Kotlin and Android"}).text)
    tool_events = [e for e in events if e["stage"] == "tool_call"]
    assert len(tool_events) == 6
    assert "get_talks_for_day" in tool_events[-1]["message"]
