"""Deterministic stand-in for a real LLM, used by the integration tests.

It answers each agent task by reading the candidate talks out of the prompt,
which exercises the real prompts, the parallel orchestration and the
post-processing without a network call. Failures and latency can be injected
with ``fake-llm.*`` properties.
"""

from __future__ import annotations

import asyncio
import re
import threading
from typing import Any

from jakarta.inject import Singleton
from micronaut.context.annotation import ConfigurationProperties, Requires
from micronaut.http.annotation import Controller, Delete, Get

from dvxaisched.agents import TASK_ALTERNATIVES, TASK_BUILD_SCHEDULE, TASK_PLAN_DAY, TASK_VALIDATE
from dvxaisched.llm import LlmClient, LlmException, LlmRequest

CANDIDATE = re.compile(
    r"- \[ID: (?P<id>\d+)\] \[(?P<day>\w+) (?P<start>\d{1,2}:\d{2})-(?P<end>\d{1,2}:\d{2}) \| Room: (?P<room>[^|]*?) \| "
    r"Track: (?P<track>[^|]*?) \| Type: (?P<type>[^|]*?) \| \d+ favs\]\n  Title: (?P<title>[^\n]*)\n  Speakers: (?P<speakers>[^\n]*)"
)
DAY_ID = re.compile(r"Day id: (\w+)\)")
DATE = re.compile(r"\(Date: ([0-9-]+),")
LABEL = re.compile(r"Day to schedule: (.*?) \(Date:")
USER_INPUT = re.compile(r"<user_input>\n(.*)\n</user_input>", re.DOTALL)
INJECTION_MARKERS = ("ignore all previous", "ignore previous", "you are dan", "system prompt", "jailbreak")


def _candidates(prompt: str) -> list[dict[str, Any]]:
    talks = []
    for m in CANDIDATE.finditer(prompt):
        talks.append({
            "talkId": int(m["id"]),
            "day": m["day"].lower(),
            "startTime": m["start"],
            "endTime": m["end"],
            "room": m["room"],
            "track": m["track"],
            "sessionType": m["type"],
            "title": m["title"],
            "speakers": m["speakers"],
            "reason": f"Fake LLM picked '{m['title']}'",
        })
    return talks


@ConfigurationProperties("fake-llm")
class FakeLlmConfig:
    fail_tasks: list[str] | None = None
    """Tasks that always fail, e.g. ``plan-day``."""
    fail_first_attempts: int = 0
    """Number of initial attempts per conference day for which ``plan-day`` fails."""
    delay_ms: int = 0
    """Simulated model latency per call."""


@Singleton
@Requires(property="llm.provider", value="fake")
class FakeLlmClient(LlmClient):
    def __init__(self, config: FakeLlmConfig | None = None):
        self.fail_tasks = {t for t in ((config.fail_tasks if config else None) or []) if t}
        self.fail_first_attempts = config.fail_first_attempts if config else 0
        self.delay_ms = config.delay_ms if config else 0
        self.calls: dict[str, int] = {}
        self.attempts: dict[str, int] = {}
        self.max_in_flight = 0
        self._in_flight = 0
        self._lock = threading.Lock()

    def model_name(self) -> str:
        return "fake-llm"

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"calls": dict(self.calls), "maxInFlight": self.max_in_flight}

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()
            self.attempts.clear()
            self.max_in_flight = 0

    async def generate_json(self, request: LlmRequest) -> dict[str, Any]:
        day_match = DAY_ID.search(request.user)
        attempt_key = f"{request.task}:{day_match.group(1) if day_match else ''}"
        with self._lock:
            self.calls[request.task] = self.calls.get(request.task, 0) + 1
            self.attempts[attempt_key] = attempt = self.attempts.get(attempt_key, 0) + 1
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            if self.delay_ms:
                await asyncio.sleep(self.delay_ms / 1000.0)
            if request.task in self.fail_tasks:
                raise LlmException(f"fake failure for {request.task}")
            if request.task == TASK_PLAN_DAY and attempt <= self.fail_first_attempts:
                raise LlmException(f"fake transient failure for {attempt_key} (attempt {attempt})")
            return self._answer(request)
        finally:
            with self._lock:
                self._in_flight -= 1

    def _answer(self, request: LlmRequest) -> dict[str, Any]:
        if request.task == TASK_VALIDATE:
            match = USER_INPUT.search(request.user)
            text = (match.group(1) if match else request.user).strip()
            if any(marker in text.lower() for marker in INJECTION_MARKERS):
                return {"valid": False, "reason": "Prompt injection attempt detected. Please enter technical topics.", "sanitizedInterests": ""}
            return {"valid": True, "reason": "", "sanitizedInterests": text}

        if request.task == TASK_PLAN_DAY:
            # Deliberately return every candidate, including overlapping ones, so
            # the workflow's de-confliction is exercised.
            return {
                "day": DAY_ID.search(request.user).group(1),
                "date": DATE.search(request.user).group(1),
                "dayLabel": LABEL.search(request.user).group(1),
                "talks": _candidates(request.user),
            }

        if request.task == TASK_BUILD_SCHEDULE:
            by_day: dict[str, list[dict[str, Any]]] = {}
            for talk in _candidates(request.user):
                by_day.setdefault(talk["day"], []).append(talk)
            return {
                "theme": "Fake monolithic schedule",
                "overview": "Built by the monolithic fallback agent",
                "days": [{"day": d, "date": "", "dayLabel": d.title(), "talks": t} for d, t in by_day.items()],
            }

        if request.task == TASK_ALTERNATIVES:
            return {"selections": [{"talkId": t["talkId"], "reason": f"Fake rationale for {t['title']}"} for t in _candidates(request.user)[:3]]}

        raise LlmException(f"unknown task {request.task}")


@Controller("/test/fake-llm")
@Requires(property="llm.provider", value="fake")
class FakeLlmController:
    def __init__(self, fake: FakeLlmClient):
        self.fake = fake

    @Get("/stats")
    def stats(self) -> dict[str, Any]:
        return self.fake.stats()

    @Delete("/stats")
    def reset(self) -> dict[str, bool]:
        self.fake.reset()
        return {"reset": True}
