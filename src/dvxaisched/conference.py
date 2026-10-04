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

"""In-memory catalog of the official Devoxx Belgium 2026 sessions."""

from __future__ import annotations

import json
import logging
import re

from jakarta.annotation import PostConstruct
from jakarta.inject import Singleton
from micronaut.context.env import Environment

from .models import ConferenceTalk, conference_talk_from_dict, speakers_summary

LOG = logging.getLogger(__name__)

SCHEDULE_RESOURCE = "devoxx-be-2026.json"

_HTML_TAG = re.compile(r"<[^>]*>")
_WHITESPACE = re.compile(r"\s+")


def has_overlap(start1: str, end1: str, start2: str, end2: str) -> bool:
    s1, e1, s2, e2 = (normalize_time(t) for t in (start1, end1, start2, end2))
    return s1 < e2 and s2 < e1


def normalize_time(time: str | None) -> str:
    if not time:
        return "00:00"
    t = time.strip()
    return f"0{t}" if re.fullmatch(r"\d:\d{2}", t) else t


@Singleton
class DevoxxConferenceService:
    def __init__(self, environment: Environment):
        # Environment is the application's classpath ResourceLoader. (ResourceResolver is
        # not yet registered for reflection in Pyronaut's native toolchain.)
        self.environment = environment
        self.talks: list[ConferenceTalk] = []

    @PostConstruct
    def load_embedded_schedule(self) -> None:
        stream = self.environment.getResourceAsStream(SCHEDULE_RESOURCE).orElse(None)
        if stream is None:
            LOG.warning("devoxx-be-2026.json resource not found")
            return
        try:
            raw = bytes(stream.readAllBytes()).decode("utf-8")
        finally:
            stream.close()
        self.talks = [conference_talk_from_dict(t) for t in json.loads(raw)]
        LOG.info("Loaded %d scheduled talks for Devoxx Belgium 2026 from embedded dataset", len(self.talks))

    def get_all_talks(self) -> list[ConferenceTalk]:
        return list(self.talks)

    def get_talk_by_id(self, talk_id: int) -> ConferenceTalk | None:
        return next((t for t in self.talks if t.id == talk_id), None)

    def get_slot_alternatives(self, talk_id: int) -> list[ConferenceTalk]:
        current = self.get_talk_by_id(talk_id)
        if current is None:
            return []
        same_day = [t for t in self.talks if t.id != current.id and t.day and t.day.lower() == current.day.lower()]

        # 1. Exact time slot match on the same day in other rooms
        exact = [t for t in same_day if t.start_time == current.start_time and t.end_time == current.end_time]
        if exact:
            return sorted(exact, key=lambda t: -t.total_favourites)

        # 2. Overlapping time slot match if no exact match (e.g. labs vs shorter sessions)
        overlapping = [
            t for t in same_day
            if t.start_time and t.end_time and has_overlap(current.start_time, current.end_time, t.start_time, t.end_time)
        ]
        return sorted(overlapping, key=lambda t: -t.total_favourites)

    def get_all_tracks(self) -> list[str]:
        return sorted({t.track for t in self.talks if t.track})

    def get_talks_by_day(self, day: str | None) -> list[ConferenceTalk]:
        if not day or not day.strip():
            return self.get_all_talks()
        normalized = day.strip().lower()
        return sorted((t for t in self.talks if t.day and t.day.lower() == normalized), key=lambda t: t.start_time)

    def search_talks(self, query: str | None, day: str | None, limit: int) -> list[ConferenceTalk]:
        effective_limit = limit if limit > 0 else 20
        if not query or not query.strip():
            return self.get_talks_by_day(day)[:effective_limit]

        keywords = query.lower().split()
        scored = []
        for t in self.talks:
            if day and day.strip() and not (t.day and t.day.lower() == day.lower()):
                continue
            score = self.score_talk(t, keywords)
            if score > 0:
                scored.append((score, t))
        # Stable sort keeps catalog order for equal scores, like the original Java stream
        scored.sort(key=lambda entry: -entry[0])
        return [t for _, t in scored[:effective_limit]]

    def score_talk(self, talk: ConferenceTalk, query: str | list[str] | None) -> int:
        if query is None or (isinstance(query, str) and not query.strip()):
            return min(talk.total_favourites, 10) if talk else 0
        keywords = query.lower().split() if isinstance(query, str) else query

        title = (talk.title or "").lower()
        summary = (talk.summary or "").lower()
        track = (talk.track or "").lower()
        speakers = speakers_summary(talk).lower()

        score = 0
        for kw in keywords:
            if len(kw) < 2:
                continue
            if kw in title:
                score += 10
            if kw in track:
                score += 8
            if kw in speakers:
                score += 6
            if kw in summary:
                score += 3
        # Add popularity weight
        return score + min(talk.total_favourites, 10)

    @staticmethod
    def format_talks_for_prompt(talks: list[ConferenceTalk]) -> str:
        lines: list[str] = []
        for t in talks:
            lines.append(
                f"- [ID: {t.id}] [{t.day.upper()} {t.start_time}-{t.end_time} | Room: {t.room} | "
                f"Track: {t.track} | Type: {t.session_type} | {t.total_favourites} favs]"
            )
            lines.append(f"  Title: {t.title}")
            lines.append(f"  Speakers: {speakers_summary(t)}")
            if t.summary and t.summary.strip():
                clean = _WHITESPACE.sub(" ", _HTML_TAG.sub(" ", t.summary)).strip()
                if len(clean) > 200:
                    clean = clean[:197] + "..."
                lines.append(f"  Summary: {clean}")
            lines.append("")
        return "\n".join(lines) + ("\n" if lines else "")
