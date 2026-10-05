# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Data model shared by the HTTP API, the conference catalog and the agents.

The HTTP API uses camelCase property names (``talkId``, ``startTime``) to stay
compatible with the web UI, while Python attributes use snake_case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Annotated, Any

from com.fasterxml.jackson.annotation import JsonProperty
from micronaut.jsonschema import JsonSchema
from micronaut.serde.annotation import Serdeable

EVENT_SLUG = "dvbe26"


def build_devoxx_talk_url(talk_id: int, title: str | None, event_slug: str = EVENT_SLUG) -> str:
    slug = event_slug or EVENT_SLUG
    base = f"https://m.devoxx.com/events/{slug}/talks/{talk_id}"
    if not title or not title.strip():
        return base
    clean = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"{base}/{clean}" if clean else base


@Serdeable
@dataclass
class SpeakerInfo:
    id: int
    name: str
    company: str = ""
    bio: str = ""


@Serdeable
@dataclass
class ConferenceTalk:
    id: int
    day: str
    date: str
    start_time: Annotated[str, JsonProperty("startTime")]
    end_time: Annotated[str, JsonProperty("endTime")]
    room: str
    title: str
    summary: str
    track: str
    session_type: Annotated[str, JsonProperty("sessionType")]
    total_favourites: Annotated[int, JsonProperty("totalFavourites")] = 0
    speakers: list[SpeakerInfo] = field(default_factory=list)
    description: str | None = None
    url: str | None = None


def talk_abstract(talk: ConferenceTalk) -> str:
    if talk.description and talk.description.strip():
        return talk.description
    return talk.summary or ""


def speakers_summary(talk: ConferenceTalk) -> str:
    if not talk.speakers:
        return "TBA"
    return ", ".join(
        f"{s.name} ({s.company})" if s.company and s.company.strip() else s.name
        for s in talk.speakers
    )


def talk_url(talk: ConferenceTalk) -> str:
    return talk.url if talk.url else build_devoxx_talk_url(talk.id, talk.title)


@Serdeable
@dataclass
class ScheduledTalk:
    talk_id: Annotated[int, JsonProperty("talkId")]
    day: str
    date: str
    start_time: Annotated[str, JsonProperty("startTime")]
    end_time: Annotated[str, JsonProperty("endTime")]
    room: str
    title: str
    speakers: str
    track: str
    session_type: Annotated[str, JsonProperty("sessionType")]
    reason: str
    talk_abstract: Annotated[str | None, JsonProperty("talkAbstract")] = None
    url: str | None = None


def scheduled_from_catalog(talk: ConferenceTalk, reason: str) -> ScheduledTalk:
    return ScheduledTalk(
        talk_id=talk.id,
        day=talk.day,
        date=talk.date,
        start_time=talk.start_time,
        end_time=talk.end_time,
        room=talk.room,
        title=talk.title,
        speakers=speakers_summary(talk),
        track=talk.track,
        session_type=talk.session_type,
        reason=reason,
        talk_abstract=talk_abstract(talk),
        url=talk_url(talk),
    )


@Serdeable
@dataclass
class DaySchedule:
    day: str
    date: str
    day_label: Annotated[str, JsonProperty("dayLabel")]
    talks: list[ScheduledTalk] = field(default_factory=list)


@Serdeable
@dataclass
class ScheduleResponse:
    valid: bool
    validation_message: Annotated[str | None, JsonProperty("validationMessage")] = None
    theme: str | None = None
    overview: str | None = None
    days: list[DaySchedule] = field(default_factory=list)


def rejected_schedule(message: str) -> ScheduleResponse:
    return ScheduleResponse(valid=False, validation_message=message)


@Serdeable
@dataclass
class ScheduleRequest:
    interests: str | None = None


@Serdeable
@dataclass
class TalkAlternativeRequest:
    interests: str | None = None
    talk_id: Annotated[int, JsonProperty("talkId")] = 0


@Serdeable
@dataclass
class TalkAlternativesResponse:
    replaced_talk_id: Annotated[int, JsonProperty("replacedTalkId")]
    replaced_title: Annotated[str | None, JsonProperty("replacedTitle")] = None
    slot_time: Annotated[str | None, JsonProperty("slotTime")] = None
    day: str | None = None
    alternatives: list[ScheduledTalk] = field(default_factory=list)
    has_alternatives: Annotated[bool, JsonProperty("hasAlternatives")] = False
    message: str | None = None


def empty_alternatives(talk_id: int, message: str) -> TalkAlternativesResponse:
    return TalkAlternativesResponse(replaced_talk_id=talk_id, message=message)


@Serdeable
@dataclass
class WorkflowProgressEvent:
    stage: str
    agent: str
    message: str
    duration_ms: Annotated[int | None, JsonProperty("durationMs")] = None
    schedule: ScheduleResponse | None = None


GUARDRAIL_AGENT = "Agent 1: Guardrail Validator"


def progress_complete(schedule: ScheduleResponse, duration_ms: int) -> WorkflowProgressEvent:
    return WorkflowProgressEvent(
        "complete", "Complete", "Your personalized Devoxx Belgium 2026 schedule is ready!", duration_ms, schedule
    )


def progress_rejected(reason: str, duration_ms: int) -> WorkflowProgressEvent:
    return WorkflowProgressEvent("rejected", GUARDRAIL_AGENT, reason, duration_ms, rejected_schedule(reason))


# ---------------------------------------------------------------------------
# Agent input and structured output types
# ---------------------------------------------------------------------------
# LangChain4j parses model answers into these classes with Jackson. The JSON
# schema sent to the model is generated at compile time from them by
# Micronaut JSON Schema (``@JsonSchema``; see ``structured_output.py``), with
# the docstrings as descriptions. They deliberately use plain snake_case
# attributes: a JsonProperty rename on a dataclass field is not carried over to
# the generated constructor parameter, which Jackson needs.


@JsonSchema
@Serdeable
@dataclass
class ValidationResult:
    """Outcome of the guardrail check of the attendee's interests."""

    valid: bool
    """true if the input is acceptable and safe, false if rejected"""
    reason: str
    """If invalid, a polite explanation and advice on what to enter instead; otherwise empty or a brief acknowledgment"""
    sanitized_interests: str | None = None
    """A cleaned up, concise representation of the user's technical interests"""


@JsonSchema
@Serdeable
@dataclass
class PlannedTalk:
    """A conference talk selected for the attendee."""

    talk_id: int
    """Official talk ID number"""
    day: str
    """Lowercase day name, e.g. monday"""
    date: str
    """Date, e.g. 2026-10-05"""
    start_time: str
    """Start time, e.g. 09:30"""
    end_time: str
    """End time, e.g. 12:30"""
    room: str
    """Room name, e.g. TBA 2"""
    title: str
    """Exact title of the talk"""
    speakers: str
    """Speaker name(s) and company"""
    track: str
    """Track name"""
    session_type: str
    """Session type, e.g. Deep Dive, Conference, Keynote, Tools-in-Action, Lunch Talk, BOF, Hands-on Lab"""
    reason: str
    """Why this talk was selected for the attendee"""


@JsonSchema
@Serdeable
@dataclass
class PlannedDay:
    """The conflict-free agenda of one conference day."""

    day: str
    """Lowercase day name, e.g. monday"""
    date: str
    """Date, e.g. 2026-10-05"""
    day_label: str
    """Human-readable label of the day"""
    talks: list[PlannedTalk] = field(default_factory=list)
    """Selected talks in chronological order"""


@JsonSchema
@Serdeable
@dataclass
class PlannedSchedule:
    """A personalized 5-day conference schedule."""

    theme: str
    """Overall theme name of the schedule"""
    overview: str
    """Motivating summary overview of the schedule"""
    days: list[PlannedDay] = field(default_factory=list)
    """The days of the schedule in chronological order"""


@JsonSchema
@Serdeable
@dataclass
class AlternativeSelection:
    """An alternative talk for the slot being replaced."""

    talk_id: int
    """Official numeric talk ID of the alternative"""
    reason: str
    """Concise 1-2 sentence rationale for the attendee"""


@JsonSchema
@Serdeable
@dataclass
class TalkAlternativesResult:
    """The selected alternative talks."""

    selections: list[AlternativeSelection] = field(default_factory=list)
    """Up to 3 distinct alternatives"""


# The @JsonSchema types whose generated schemas are sent to the chat model
STRUCTURED_OUTPUT_TYPES: list[type] = [
    ValidationResult,
    PlannedTalk,
    PlannedDay,
    PlannedSchedule,
    AlternativeSelection,
    TalkAlternativesResult,
]


@Serdeable
@dataclass
class DayPlanRequest:
    """Input of one parallel day planner; rendered into the prompt with ``str()``."""

    day: str
    date: str
    day_label: str
    interests: str
    candidate_talks: str

    def __str__(self) -> str:
        return (
            f"User interests: {self.interests}\n\n"
            f"Day to schedule: {self.day_label} (Date: {self.date}, Day id: {self.day})\n\n"
            f"Available Devoxx Belgium candidate talks for {self.day_label}:\n"
            f"{self.candidate_talks}\n\n"
            f"Instructions for {self.day_label}:\n"
            "- Select non-overlapping talks strictly in chronological order "
            "(each talk's startTime >= previous talk's endTime).\n"
            "- Do NOT pick multiple talks in parallel rooms for the same time slot.\n"
            "- Provide a full day's agenda matching the attendee's interests.\n"
        )


def scheduled_from_plan(talk: PlannedTalk) -> ScheduledTalk:
    return ScheduledTalk(
        talk_id=talk.talk_id,
        day=(talk.day or "").lower(),
        date=talk.date or "",
        start_time=talk.start_time or "",
        end_time=talk.end_time or "",
        room=talk.room or "",
        title=talk.title or "",
        speakers=talk.speakers or "",
        track=talk.track or "",
        session_type=talk.session_type or "",
        reason=talk.reason or "",
    )


def day_from_plan(day: PlannedDay) -> DaySchedule:
    return DaySchedule(
        day=(day.day or "").lower(),
        date=day.date or "",
        day_label=day.day_label or "",
        talks=[scheduled_from_plan(t) for t in (day.talks or []) if t is not None],
    )


# ---------------------------------------------------------------------------
# Parsing of the embedded catalog file
# ---------------------------------------------------------------------------


def _str(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else (default if value is None else str(value))


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def conference_talk_from_dict(data: dict[str, Any]) -> ConferenceTalk:
    return ConferenceTalk(
        id=_int(data.get("id")),
        day=_str(data.get("day")),
        date=_str(data.get("date")),
        start_time=_str(data.get("startTime")),
        end_time=_str(data.get("endTime")),
        room=_str(data.get("room"), "TBA"),
        title=_str(data.get("title")),
        summary=_str(data.get("summary")),
        track=_str(data.get("track")),
        session_type=_str(data.get("sessionType")),
        total_favourites=_int(data.get("totalFavourites")),
        speakers=[
            SpeakerInfo(_int(s.get("id")), _str(s.get("name")), _str(s.get("company")), _str(s.get("bio")))
            for s in data.get("speakers") or []
            if isinstance(s, dict)
        ],
        description=data.get("description") or None,
        url=data.get("url") or None,
    )
