"""Each day planner fails once and is retried without discarding the other days."""

from sse import sse_events

CONTEXT_PROPERTIES = {"fake-llm.fail-first-attempts": 1}


def test_transient_day_failures_are_retried(client):
    events = sse_events(client.get("/api/schedule/stream", params={"interests": "Java performance"}).text)
    retries = [e for e in events if e["stage"] == "agent2_progress" and "Retrying" in e["message"]]
    assert len(retries) == 5, "Each day worker should report one retry"

    schedule = events[-1]["schedule"]
    assert schedule["valid"] is True
    assert schedule["theme"].startswith("Devoxx Belgium 2026:"), "The parallel planners should still produce the schedule"
    assert client.get("/test/fake-llm/stats").json()["calls"]["plan-day"] == 10
