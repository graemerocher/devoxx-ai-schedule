"""Deterministic LangChain4j ``ChatModel`` used by the integration tests.

It answers each agent by reading the candidate talks out of the prompt, which
exercises the real agents, prompts, structured outputs, tool calling, the
parallel mapper and the post-processing without a network call. Failures and
latency can be injected per test module with ``fake-llm.*`` properties.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from dev.langchain4j.agent.tool import ToolExecutionRequest
from dev.langchain4j.data.message import AiMessage
from dev.langchain4j.model.chat import ChatModel
from dev.langchain4j.model.chat.request import ChatRequest, ChatRequestParameters
from dev.langchain4j.model.chat.response import ChatResponse
from jakarta.inject import Singleton
from java.util import List
from micronaut.context.annotation import ConfigurationProperties, Primary, Requires
from micronaut.http.annotation import Controller, Delete, Get

TASK_VALIDATE = "validate-interests"
TASK_PLAN_DAY = "plan-day"
TASK_BUILD_SCHEDULE = "build-schedule"
TASK_ALTERNATIVES = "recommend-alternatives"

TASK_MARKERS = {
    "strict security and validation guardrail": TASK_VALIDATE,
    "conflict-free schedule for a SINGLE conference day": TASK_PLAN_DAY,
    "complete, personalized, conflict-free conference agenda": TASK_BUILD_SCHEDULE,
    "wants to replace a scheduled talk": TASK_ALTERNATIVES,
}

CANDIDATE = re.compile(
    r"- \[ID: (?P<id>\d+)\] \[(?P<day>\w+) (?P<start>\d{1,2}:\d{2})-(?P<end>\d{1,2}:\d{2}) \| Room: (?P<room>[^|]*?) \| "
    r"Track: (?P<track>[^|]*?) \| Type: (?P<type>[^|]*?) \| \d+ favs\]\n\s*Title: (?P<title>[^\n]*)\n\s*Speakers: (?P<speakers>[^\n]*)"
)
DAY_ID = re.compile(r"Day id: (\w+)\)")
DATE = re.compile(r"\(Date: ([0-9-]+),")
LABEL = re.compile(r"Day to schedule: (.*?) \(Date:")
USER_INPUT = re.compile(r"<user_input>\n(.*)\n</user_input>", re.DOTALL)
INJECTION_MARKERS = ("ignore all previous", "ignore previous", "you are dan", "system prompt", "jailbreak")
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")


def task_for(system: str) -> str:
    return next((task for marker, task in TASK_MARKERS.items() if marker in system), "unknown")


def candidates(text: str) -> list[dict[str, Any]]:
    return [
        {
            "talk_id": int(m["id"]),
            "day": m["day"].lower(),
            "date": "",
            "start_time": m["start"],
            "end_time": m["end"],
            "room": m["room"],
            "title": m["title"],
            "speakers": m["speakers"],
            "track": m["track"],
            "session_type": m["type"],
            "reason": f"Fake LLM picked '{m['title']}'",
        }
        for m in CANDIDATE.finditer(text)
    ]


def answer(task: str, user: str, tool_results: list[str]) -> dict[str, Any]:
    """The structured answer for an agent task, as the JSON object a model would return."""
    if task == TASK_VALIDATE:
        match = USER_INPUT.search(user)
        text = (match.group(1) if match else user).strip()
        if any(marker in text.lower() for marker in INJECTION_MARKERS):
            return {"valid": False, "reason": "Prompt injection attempt detected. Please enter technical topics.", "sanitized_interests": ""}
        return {"valid": True, "reason": "", "sanitized_interests": text}

    if task == TASK_PLAN_DAY:
        # Deliberately return every candidate, including overlapping ones, so
        # the workflow's de-confliction is exercised.
        return {
            "day": DAY_ID.search(user).group(1),
            "date": DATE.search(user).group(1),
            "day_label": LABEL.search(user).group(1),
            "talks": candidates(user),
        }

    if task == TASK_BUILD_SCHEDULE:
        by_day: dict[str, list[dict[str, Any]]] = {}
        for talk in candidates("\n".join(tool_results)):
            by_day.setdefault(talk["day"], []).append(talk)
        return {
            "theme": "Fake monolithic schedule",
            "overview": f"Built by the monolithic fallback agent from {len(tool_results)} tool results",
            "days": [{"day": d, "date": "", "day_label": d.title(), "talks": t} for d, t in by_day.items()],
        }

    if task == TASK_ALTERNATIVES:
        return {"selections": [{"talk_id": t["talk_id"], "reason": f"Fake rationale for {t['title']}"} for t in candidates(user)[:3]]}

    raise RuntimeError(f"unknown task {task}")


@ConfigurationProperties("fake-llm")
class FakeLlmConfig:
    fail_tasks: list[str] | None = None
    """Tasks that always fail, e.g. ``plan-day``."""
    fail_first_attempts: int = 0
    """Number of initial attempts per conference day for which ``plan-day`` fails."""
    delay_ms: int = 0
    """Simulated model latency per call."""


@Singleton
@Primary
@Requires(env="test")
@Requires(property="langchain4j.google-ai-gemini.enabled", value="false")
class FakeChatModel(ChatModel):
    def __init__(self, config: FakeLlmConfig):
        self.fail_tasks = {t for t in (config.fail_tasks or []) if t}
        self.fail_first_attempts = config.fail_first_attempts
        self.delay_ms = config.delay_ms
        self._lock = threading.Lock()
        self.reset()

    def defaultRequestParameters(self):
        return ChatRequestParameters.builder().modelName("fake-chat-model").build()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self.calls: dict[str, int] = {}
            self.attempts: dict[str, int] = {}
            self.schemas: dict[str, list[str]] = {}
            self.descriptions: dict[str, str] = {}
            self.tools_called: list[str] = []
            self.max_in_flight = 0
            self._in_flight = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "calls": dict(self.calls),
                "maxInFlight": self.max_in_flight,
                "schemas": dict(self.schemas),
                "descriptions": dict(self.descriptions),
                "toolsCalled": list(self.tools_called),
            }

    def doChat(self, chat_request: ChatRequest) -> ChatResponse:
        system, user, tool_results = "", "", []
        for message in chat_request.messages():
            kind = str(message.type())
            if kind == "SYSTEM":
                system = str(message.text())
            elif kind == "USER":
                user = str(message.singleText())
            elif kind == "TOOL_EXECUTION_RESULT":
                tool_results.append(str(message.text()))
        task = task_for(system)
        day = DAY_ID.search(user)
        attempt_key = f"{task}:{day.group(1) if day else ''}"

        with self._lock:
            self.calls[task] = self.calls.get(task, 0) + 1
            self.attempts[attempt_key] = attempt = self.attempts.get(attempt_key, 0) + 1
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
            response_format = chat_request.responseFormat()
            if response_format is not None and response_format.jsonSchema() is not None:
                root = response_format.jsonSchema().rootElement()
                self.schemas[task] = sorted(str(k) for k in root.properties().keySet())
                self.descriptions[task] = str(root.description())
        try:
            if self.delay_ms:
                time.sleep(self.delay_ms / 1000.0)
            if task in self.fail_tasks:
                raise RuntimeError(f"fake failure for {task}")
            if task == TASK_PLAN_DAY and attempt <= self.fail_first_attempts:
                raise RuntimeError(f"fake transient failure for {attempt_key} (attempt {attempt})")

            if task == TASK_BUILD_SCHEDULE and not tool_results:
                # First round trip: ask for the catalog through the agent's tools
                requests = [ToolExecutionRequest.builder().id("tracks").name("get_conference_tracks").arguments("{}").build()]
                requests += [
                    ToolExecutionRequest.builder().id(d).name("get_talks_for_day").arguments(json.dumps({"day": d})).build()
                    for d in DAYS
                ]
                with self._lock:
                    self.tools_called.extend(r.name() for r in requests)
                return ChatResponse.builder().aiMessage(AiMessage.from_(List.of(*requests))).build()

            text = json.dumps(answer(task, user, tool_results))
            return ChatResponse.builder().aiMessage(AiMessage.from_(text)).build()
        finally:
            with self._lock:
                self._in_flight -= 1


@Controller("/test/fake-llm")
@Requires(env="test")
@Requires(property="langchain4j.google-ai-gemini.enabled", value="false")
class FakeLlmController:
    def __init__(self, chat_model: ChatModel):
        # The bean is decorated by dvxaisched.structured_output.SchemaAwareChatModel
        self.fake = getattr(chat_model, "delegate", chat_model)

    @Get("/stats")
    def stats(self) -> dict[str, Any]:
        return self.fake.stats()

    @Delete("/stats")
    def reset(self) -> dict[str, bool]:
        self.fake.reset()
        return {"reset": True}
