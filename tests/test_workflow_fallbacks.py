"""When the parallel day planners fail, the monolithic agent takes over."""

from sse import assert_conflict_free

CONTEXT_PROPERTIES = {"fake-llm.fail-tasks": "plan-day"}


def test_monolithic_fallback(client):
    body = client.post("/api/schedule", json={"interests": "Security and supply chain"}).json()
    assert body["valid"] is True
    assert body["theme"] == "Fake monolithic schedule"
    assert any(d["talks"] for d in body["days"])
    assert_conflict_free(body)

    calls = client.get("/test/fake-llm/stats").json()["calls"]
    assert calls["plan-day"] == 15, "Each of the 5 days is attempted 3 times"
    assert calls["build-schedule"] == 1
