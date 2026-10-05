import pytest

from dvxaisched.conference import DevoxxConferenceService, has_overlap


@pytest.fixture(scope="module")
def conference_service(app_context):
    service = DevoxxConferenceService(app_context["io.micronaut.context.env.Environment"])
    service.load_embedded_schedule()
    return service


def test_talks_are_loaded(conference_service):
    talks = conference_service.get_all_talks()
    assert talks, "Conference talks should be loaded from devoxx-be-2026.json"
    assert len(talks) >= 190, f"Expected around 200 talks, found {len(talks)}"


def test_tracks_exist(conference_service):
    tracks = conference_service.get_all_tracks()
    assert "Agentic Engineering & Tooling" in tracks
    assert "Java Language & Platform" in tracks
    assert "Mind the Geek" in tracks
    assert tracks == sorted(tracks)


def test_search_talks(conference_service):
    results = conference_service.search_talks("agent loop", None, 10)
    assert results, "Should find talks matching 'agent loop'"
    assert len(results) <= 10
    assert any("loop" in t.title.lower() or "agent" in t.title.lower() for t in results)


def test_day_filtering(conference_service):
    monday = conference_service.get_talks_by_day("monday")
    assert monday
    assert all(t.day.lower() == "monday" for t in monday)
    assert [t.start_time for t in monday] == sorted(t.start_time for t in monday)


def test_get_slot_alternatives(conference_service):
    target = next(
        t for t in conference_service.get_talks_by_day("wednesday") if t.start_time == "14:00" and t.end_time == "14:50"
    )
    alternatives = conference_service.get_slot_alternatives(target.id)
    assert len(alternatives) >= 5, "Expected at least 5 parallel rooms for 14:00 slot"
    for alt in alternatives:
        assert alt.id != target.id
        assert alt.day.lower() == "wednesday"
        assert (alt.start_time, alt.end_time) == (target.start_time, target.end_time)

    assert conference_service.get_slot_alternatives(9999999) == []


def test_prompt_formatting(conference_service):
    talk = conference_service.get_talk_by_id(7006)
    prompt = conference_service.format_talks_for_prompt([talk])
    assert prompt.startswith(f"- [ID: 7006] [{talk.day.upper()} {talk.start_time}-{talk.end_time} | Room: {talk.room}")
    assert f"  Title: {talk.title}" in prompt
    assert "<" not in prompt.split("Summary: ", 1)[1]


def test_overlap():
    assert has_overlap("09:30", "12:30", "12:00", "12:50")
    assert not has_overlap("09:30", "10:20", "10:20", "11:00")
    assert has_overlap("9:30", "10:20", "10:00", "10:30")
