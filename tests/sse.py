"""Helpers shared by the HTTP integration tests."""

import json


def sse_events(text: str) -> list[dict]:
    """Parses a buffered text/event-stream body into its JSON data payloads."""
    events = []
    for block in text.split("\n\n"):
        data = "".join(line[len("data:"):].strip() for line in block.splitlines() if line.startswith("data:"))
        if data:
            events.append(json.loads(data))
    return events


def assert_conflict_free(schedule: dict) -> None:
    for day in schedule["days"]:
        talks = day["talks"]
        for current, following in zip(talks, talks[1:]):
            assert current["endTime"] <= following["startTime"], (
                f"Talk '{current['title']}' ({current['endTime']}) overlaps with "
                f"'{following['title']}' ({following['startTime']}) on {day['dayLabel']}"
            )
