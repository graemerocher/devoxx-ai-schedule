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

The JSON wire format uses camelCase property names (``talkId``, ``startTime``)
to stay compatible with the web UI, while Python attributes use snake_case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Annotated, Any

from com.fasterxml.jackson.annotation import JsonProperty
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
# Agent-internal structures (never serialized by Micronaut)
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    valid: bool
    reason: str = ""
    sanitized_interests: str | None = None


@dataclass
class DayPlanRequest:
    day: str
    date: str
    day_label: str
    interests: str
    candidate_talks: str

    def to_prompt(self) -> str:
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


@dataclass
class AlternativeSelection:
    talk_id: int
    reason: str = ""


# ---------------------------------------------------------------------------
# Parsing helpers for JSON produced by the catalog file and by LLMs
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


def scheduled_talk_from_dict(data: dict[str, Any]) -> ScheduledTalk:
    return ScheduledTalk(
        talk_id=_int(data.get("talkId")),
        day=_str(data.get("day")).lower(),
        date=_str(data.get("date")),
        start_time=_str(data.get("startTime")),
        end_time=_str(data.get("endTime")),
        room=_str(data.get("room")),
        title=_str(data.get("title")),
        speakers=_str(data.get("speakers")),
        track=_str(data.get("track")),
        session_type=_str(data.get("sessionType")),
        reason=_str(data.get("reason")),
        talk_abstract=data.get("talkAbstract") or None,
        url=data.get("url") or None,
    )


def day_schedule_from_dict(data: dict[str, Any]) -> DaySchedule:
    return DaySchedule(
        day=_str(data.get("day")).lower(),
        date=_str(data.get("date")),
        day_label=_str(data.get("dayLabel")),
        talks=[scheduled_talk_from_dict(t) for t in data.get("talks") or [] if isinstance(t, dict)],
    )
