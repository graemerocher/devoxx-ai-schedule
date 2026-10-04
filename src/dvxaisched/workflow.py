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

"""Agentic scheduling pipeline.

1. ``InterestValidatorAgent`` guards against prompt injection and off-topic input.
2. The catalog is pre-scored and partitioned into the five conference days.
3. Five ``DayScheduleBuilderAgent`` calls run concurrently on the Netty event
   loop with ``asyncio.gather``; each day is retried independently.
4. If the parallel stage fails, the monolithic ``ScheduleBuilderAgent`` is used,
   and if every LLM call fails a deterministic catalog schedule is returned.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from jakarta.inject import Singleton

from .agents import DayScheduleBuilderAgent, InterestValidatorAgent, ScheduleBuilderAgent, TalkAlternativeAgent
from .conference import DevoxxConferenceService, has_overlap, normalize_time
from .models import (
    GUARDRAIL_AGENT,
    ConferenceTalk,
    DayPlanRequest,
    DaySchedule,
    ScheduledTalk,
    ScheduleResponse,
    TalkAlternativesResponse,
    ValidationResult,
    WorkflowProgressEvent,
    build_devoxx_talk_url,
    empty_alternatives,
    progress_complete,
    progress_rejected,
    rejected_schedule,
    scheduled_from_catalog,
    talk_abstract,
)

LOG = logging.getLogger(__name__)

ProgressListener = Callable[[WorkflowProgressEvent], None]

MAX_INTERESTS_LENGTH = 500
MAX_DAY_RETRIES = 2
ALTERNATIVES_TIMEOUT_SECONDS = 6.0

CONFERENCE_DAYS: list[tuple[str, str, str]] = [
    ("monday", "2026-10-05", "Monday, Oct 5 (Deep Dives & Labs)"),
    ("tuesday", "2026-10-06", "Tuesday, Oct 6 (Deep Dives & Labs)"),
    ("wednesday", "2026-10-07", "Wednesday, Oct 7 (Keynotes & Conference)"),
    ("thursday", "2026-10-08", "Thursday, Oct 8 (Conference)"),
    ("friday", "2026-10-09", "Friday, Oct 9 (Conference - Half Day)"),
]

DAY_SEARCH_LIMITS = {"monday": 20, "tuesday": 20, "wednesday": 25, "thursday": 25, "friday": 15}
DAY_MIN_TALKS = {"monday": 3, "tuesday": 3, "wednesday": 5, "thursday": 5, "friday": 3}

AGENT2 = "Agent 2: Schedule Optimizer"
PARALLEL_AGENTS = "Parallel Day Optimizers"
PARALLEL_AGENT = "Parallel Day Optimizer"


def sanitize_for_log(value: str | None) -> str:
    return (value or "").replace("\r", " ").replace("\n", " ").strip()


def _millis_since(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


@Singleton
class DevoxxAgentWorkflowService:
    def __init__(
        self,
        conference_service: DevoxxConferenceService,
        validator_agent: InterestValidatorAgent,
        day_schedule_agent: DayScheduleBuilderAgent,
        schedule_builder_agent: ScheduleBuilderAgent,
        alternative_agent: TalkAlternativeAgent,
    ):
        self.conference_service = conference_service
        self.validator_agent = validator_agent
        self.day_schedule_agent = day_schedule_agent
        self.schedule_builder_agent = schedule_builder_agent
        self.alternative_agent = alternative_agent

    async def process_schedule_request(
        self, user_interests: str | None, progress: ProgressListener | None = None
    ) -> ScheduleResponse:
        emit: ProgressListener = progress or (lambda event: None)

        raw_input = (user_interests or "").strip()
        if not raw_input:
            message = "Please enter one or more topics, technologies, or themes you are interested in."
            emit(progress_rejected(message, 0))
            return rejected_schedule(message)
        if len(raw_input) > MAX_INTERESTS_LENGTH:
            message = (
                f"Input exceeds maximum allowed length of {MAX_INTERESTS_LENGTH} characters. "
                "Please provide a concise summary of your topics."
            )
            emit(progress_rejected(message, 0))
            return rejected_schedule(message)

        total_start = time.monotonic()

        # Step 1: guardrail validation
        LOG.info("Step 1 [Agent 1 - Validator]: Validating input '%s'", sanitize_for_log(raw_input))
        emit(WorkflowProgressEvent(
            "agent1_start", GUARDRAIL_AGENT,
            "Analyzing input for safety, prompt injection defenses, and technical relevance...",
        ))
        a1_start = time.monotonic()
        try:
            validation = await self.validator_agent.validate(raw_input)
        except Exception as e:
            LOG.error("Error executing InterestValidatorAgent: %s", e)
            validation = self._fallback_validation()
        a1_duration = _millis_since(a1_start)
        LOG.info(
            "Agent 1 Result in %dms: valid=%s, reason='%s', sanitized='%s'",
            a1_duration, validation.valid, validation.reason, validation.sanitized_interests,
        )

        if not validation.valid:
            message = validation.reason.strip() or (
                "The request could not be accepted. Please enter topics related to software engineering or technology."
            )
            emit(progress_rejected(message, a1_duration))
            return ScheduleResponse(valid=False, validation_message=message, theme=raw_input)

        query = (validation.sanitized_interests or "").strip() or raw_input
        emit(WorkflowProgressEvent(
            "agent1_done", GUARDRAIL_AGENT,
            f"Passed guardrail in {a1_duration / 1000.0:.1f}s! Topics: {query}", a1_duration,
        ))

        # Step 2: partition candidate talks into the five conference days
        LOG.info("Step 2 [Parallel Day Optimizers]: Partitioning catalog into 5 days for query '%s'", query)
        emit(WorkflowProgressEvent(
            "indexing", "Devoxx Catalog Indexer", "Partitioning candidate sessions across all 5 conference days..."
        ))
        day_requests, all_candidates = self._partition_candidates(query)

        emit(WorkflowProgressEvent(
            "agent2_start", PARALLEL_AGENTS,
            "Dispatching 5 parallel AI workers to synthesize Mon–Fri concurrently...",
        ))
        a2_start = time.monotonic()
        final_response: ScheduleResponse | None = None

        try:
            raw_days = await asyncio.gather(*(self._plan_day_with_retries(r, emit) for r in day_requests))
            if any(d.talks for d in raw_days):
                days = self._enrich_days_with_abstracts(self._deconflict_and_backfill_days(raw_days, query))
                final_response = ScheduleResponse(
                    valid=True,
                    theme=f"Devoxx Belgium 2026: {query}",
                    overview=f"AI-curated conflict-free 5-day conference agenda tailored to your focus on {query}.",
                    days=days,
                )
                LOG.info("Parallel day optimizers completed successfully with %d days", len(days))
        except Exception as e:
            LOG.error("Parallel day optimizers failed, trying fallback: %s", e)

        if final_response is None:
            final_response = await self._monolithic_fallback(query, all_candidates, emit)

        if final_response is None:
            final_response = self._build_fallback_schedule(query, all_candidates)

        a2_duration = _millis_since(a2_start)
        total_duration = _millis_since(total_start)
        LOG.info("Agent 2 finished in %dms. Total curation duration: %dms", a2_duration, total_duration)

        emit(WorkflowProgressEvent(
            "agent2_done", AGENT2, f"5-day timetable synthesized in {a2_duration / 1000.0:.1f}s!", a2_duration
        ))
        emit(progress_complete(final_response, total_duration))
        return final_response

    def _partition_candidates(self, query: str) -> tuple[list[DayPlanRequest], list[ConferenceTalk]]:
        day_requests: list[DayPlanRequest] = []
        all_candidates: list[ConferenceTalk] = []
        for day, date, label in CONFERENCE_DAYS:
            matches = self.conference_service.search_talks(query, day, DAY_SEARCH_LIMITS[day])
            if len(matches) < 10:
                seen = {t.id for t in matches}
                for top in self.conference_service.get_talks_by_day(day):
                    if top.id not in seen:
                        matches.append(top)
                        seen.add(top.id)
                    if len(matches) >= 15:
                        break
            all_candidates.extend(matches)
            prompt = self.conference_service.format_talks_for_prompt(matches)
            day_requests.append(DayPlanRequest(day, date, label, query, prompt))
        return day_requests, all_candidates

    async def _plan_day_with_retries(self, request: DayPlanRequest, emit: ProgressListener) -> DaySchedule:
        attempt = 0
        while True:
            try:
                day = await self.day_schedule_agent.build_day_schedule(request)
                emit(WorkflowProgressEvent(
                    "agent2_progress", PARALLEL_AGENT, f"Curated {request.day_label} concurrently"
                ))
                return day
            except Exception as e:
                if attempt >= MAX_DAY_RETRIES:
                    LOG.error("Day worker '%s' exceeded max retries: %s", request.day, e)
                    raise
                attempt += 1
                LOG.warning(
                    "Error in day worker '%s' (attempt %d of %d): %s. Retrying...",
                    request.day, attempt, MAX_DAY_RETRIES, e,
                )
                emit(WorkflowProgressEvent(
                    "agent2_progress", PARALLEL_AGENT,
                    f"Transient issue on day worker ({request.day}). Retrying (attempt {attempt})...",
                ))

    async def _monolithic_fallback(
        self, query: str, candidates: list[ConferenceTalk], emit: ProgressListener
    ) -> ScheduleResponse | None:
        try:
            LOG.warning("Falling back to monolithic ScheduleBuilderAgent")
            emit(WorkflowProgressEvent(
                "agent2_start", AGENT2, "Curating conflict-free conference timetable in a single pass..."
            ))
            unique = list({t.id: t for t in candidates}.values())
            prompt = self.conference_service.format_talks_for_prompt(unique)
            response = await self.schedule_builder_agent.build_schedule(query, prompt)
            if any(d.talks for d in response.days):
                days = self._enrich_days_with_abstracts(self._deconflict_and_backfill_days(response.days, query))
                return ScheduleResponse(
                    valid=True, theme=response.theme or query, overview=response.overview, days=days
                )
        except Exception as e:
            LOG.error("Error executing ScheduleBuilderAgent fallback: %s", e)
        return None

    def _enrich_days_with_abstracts(self, days: list[DaySchedule]) -> list[DaySchedule]:
        """Replaces model-provided abstracts and URLs with the authoritative catalog values."""
        enriched: list[DaySchedule] = []
        for day in days:
            talks: list[ScheduledTalk] = []
            for talk in day.talks:
                catalog = self.conference_service.get_talk_by_id(talk.talk_id)
                real_abstract = talk_abstract(catalog) if catalog else ""
                if not real_abstract.strip():
                    title = (talk.title or "").strip().lower()
                    by_title = next(
                        (t for t in self.conference_service.talks if title and t.title and t.title.lower() == title),
                        None,
                    )
                    real_abstract = talk_abstract(by_title) if by_title else (talk.talk_abstract or "")

                if catalog and catalog.url:
                    real_url = catalog.url
                elif talk.url and talk.url.startswith("https://m.devoxx.com/"):
                    real_url = talk.url
                else:
                    real_url = build_devoxx_talk_url(talk.talk_id, talk.title)

                talk.talk_abstract = real_abstract
                talk.url = real_url
                talks.append(talk)
            enriched.append(DaySchedule(day.day, day.date, day.day_label, talks))
        return enriched

    @staticmethod
    def _fallback_validation() -> ValidationResult:
        LOG.warning("InterestValidatorAgent encountered an error or timeout; rejecting request by default (fail-closed)")
        return ValidationResult(
            valid=False,
            reason="The safety validation service is temporarily unavailable. Please retry in a few moments with your conference topics.",
        )

    def _build_fallback_schedule(self, query: str, talks: list[ConferenceTalk]) -> ScheduleResponse:
        LOG.info("Building fallback structured schedule for query '%s'", query)
        days = []
        for day, date, label in CONFERENCE_DAYS:
            scheduled = [
                scheduled_from_catalog(t, f"Matches your interest in {query}")
                for t in talks
                if t.day.lower() == day
            ]
            days.append(DaySchedule(day, date, label, scheduled))
        return ScheduleResponse(
            valid=True,
            theme=f"Devoxx Belgium 2026: {query}",
            overview=f"Personalized schedule curated for interests in {query}",
            days=self._deconflict_and_backfill_days(days, query),
        )

    def _deconflict_and_backfill_days(self, days: list[DaySchedule], query: str) -> list[DaySchedule]:
        return [self._deconflict_and_backfill_day(d, query) for d in days if d is not None]

    def _deconflict_and_backfill_day(self, day_schedule: DaySchedule, query: str) -> DaySchedule:
        """Removes overlapping talks, backfills empty slots and orders talks chronologically."""
        day_name = (day_schedule.day or "").lower()
        raw = sorted(
            (t for t in day_schedule.talks if t is not None and t.start_time and t.end_time),
            key=lambda t: normalize_time(t.start_time),
        )

        accepted: list[ScheduledTalk] = []
        for talk in raw:
            clash = next(
                (a for a in accepted if has_overlap(talk.start_time, talk.end_time, a.start_time, a.end_time)), None
            )
            if clash is not None:
                LOG.warning(
                    "Schedule conflict detected on %s: talk '%s' (%s-%s) overlaps with '%s' (%s-%s). Discarding duplicate.",
                    day_name, talk.title, talk.start_time, talk.end_time, clash.title, clash.start_time, clash.end_time,
                )
                continue
            accepted.append(talk)

        target_min = DAY_MIN_TALKS.get(day_name, 3)
        if len(accepted) < target_min:
            candidates = self.conference_service.search_talks(query, day_name, 30)
            if len(candidates) < 10:
                seen = {c.id for c in candidates}
                candidates.extend(t for t in self.conference_service.get_talks_by_day(day_name) if t.id not in seen)

            for candidate in candidates:
                if len(accepted) >= target_min:
                    break
                if not candidate.start_time or not candidate.end_time:
                    continue
                if any(a.talk_id == candidate.id for a in accepted):
                    continue
                if any(has_overlap(candidate.start_time, candidate.end_time, a.start_time, a.end_time) for a in accepted):
                    continue
                LOG.info(
                    "Backfilling open slot %s-%s on %s with talk '%s' (ID: %d)",
                    candidate.start_time, candidate.end_time, day_name, candidate.title, candidate.id,
                )
                backfill = scheduled_from_catalog(
                    candidate, f"Recommended session fitting your schedule and matching your interest in {query}."
                )
                backfill.day = day_name
                accepted.append(backfill)

        accepted.sort(key=lambda t: normalize_time(t.start_time))
        return DaySchedule(day_schedule.day, day_schedule.date, day_schedule.day_label, accepted)

    async def find_alternatives(self, user_interests: str | None, talk_id: int) -> TalkAlternativesResponse:
        """Finds and curates the top 3 alternative talks for an attendee's slot."""
        current = self.conference_service.get_talk_by_id(talk_id)
        if current is None:
            return empty_alternatives(talk_id, f"Talk #{talk_id} was not found in the conference catalog.")
        slot_time = f"{current.start_time} – {current.end_time}"

        candidates = self.conference_service.get_slot_alternatives(talk_id)
        if not candidates:
            return TalkAlternativesResponse(
                replaced_talk_id=talk_id,
                replaced_title=current.title,
                slot_time=slot_time,
                day=current.day,
                message="This session is a plenary event (e.g. Keynote) with no parallel tracks scheduled in other rooms.",
            )

        interests = (user_interests or "").strip() or "Software engineering, modern technology, and cloud development"
        selected: list[ScheduledTalk] = []
        selected_ids: set[int] = set()

        # 1. Ask the LLM for a recommendation, bounded by a timeout
        try:
            selections = await asyncio.wait_for(
                self.alternative_agent.recommend_alternatives(
                    interests,
                    current.id,
                    current.title,
                    current.track or "General",
                    current.room or "Main",
                    self.conference_service.format_talks_for_prompt(candidates),
                ),
                ALTERNATIVES_TIMEOUT_SECONDS,
            )
            for selection in selections:
                if len(selected) >= 3:
                    break
                if selection.talk_id <= 0 or selection.talk_id == current.id or selection.talk_id in selected_ids:
                    continue
                match = next((c for c in candidates if c.id == selection.talk_id), None)
                if match is None:
                    match = self.conference_service.get_talk_by_id(selection.talk_id)
                if match is None:
                    continue
                selected_ids.add(match.id)
                reason = selection.reason.strip() or f"Recommended alternative in the {match.track or 'technical'} track."
                selected.append(scheduled_from_catalog(match, reason))
        except (asyncio.TimeoutError, TimeoutError):
            LOG.warning("AlternativeAgent timed out for talk %d. Falling back to deterministic ranking.", talk_id)
        except Exception as e:
            LOG.warning("AlternativeAgent invocation failed for talk %d: %s. Falling back to deterministic ranking.", talk_id, e)

        # 2. Backfill with deterministic candidate ranking if the LLM returned fewer than the target
        target = min(3, len(candidates))
        if len(selected) < target:
            ranked = sorted(candidates, key=lambda t: -self.conference_service.score_talk(t, interests))
            for t in ranked:
                if len(selected) >= target:
                    break
                if t.id in selected_ids:
                    continue
                selected_ids.add(t.id)
                selected.append(scheduled_from_catalog(
                    t, f"Alternative in {t.track or 'the conference'} with high community interest."
                ))

        return TalkAlternativesResponse(
            replaced_talk_id=talk_id,
            replaced_title=current.title,
            slot_time=slot_time,
            day=current.day,
            alternatives=selected,
            has_alternatives=bool(selected),
        )
