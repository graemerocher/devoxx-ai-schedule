"""Mock Gemini and OpenAI-compatible HTTP APIs served by the application under test.

Enabled with ``mock-llm-api.enabled=true``; the real ``GeminiLlmClient`` /
``OpenAiCompatibleLlmClient`` are then pointed at these routes so the request
and response mapping is tested over HTTP without network access. Answers are
produced by the same deterministic logic as the fake LLM.
"""

from __future__ import annotations

import json
import threading
from typing import Annotated, Any

from jakarta.inject import Singleton
from micronaut.context.annotation import Requires
from micronaut.http import HttpRequest, HttpResponse
from micronaut.http.annotation import Body, Controller, Get, Post

from dvxaisched import agents
from dvxaisched.llm import LlmRequest
from fake_llm import FakeLlmClient

TASKS_BY_SYSTEM_PROMPT = {
    agents.VALIDATOR_SYSTEM: agents.TASK_VALIDATE,
    agents.DAY_BUILDER_SYSTEM: agents.TASK_PLAN_DAY,
    agents.SCHEDULE_BUILDER_SYSTEM: agents.TASK_BUILD_SCHEDULE,
    agents.ALTERNATIVES_SYSTEM: agents.TASK_ALTERNATIVES,
}


@Singleton
@Requires(property="mock-llm-api.enabled", value="true")
class MockLlmApiState:
    def __init__(self):
        self.requests: list[dict[str, Any]] = []
        self.answers = FakeLlmClient()
        self._lock = threading.Lock()

    def record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.requests.append(entry)

    def answer(self, system: str, user: str) -> dict[str, Any]:
        task = TASKS_BY_SYSTEM_PROMPT[system]
        return self.answers._answer(LlmRequest(task, system, user, {}))


@Controller("/mock-api")
@Requires(property="mock-llm-api.enabled", value="true")
class MockLlmApiController:
    def __init__(self, state: MockLlmApiState):
        self.state = state

    @Post("/gemini/v1beta/models/{+path}")
    def gemini_generate_content(self, path: str, body: Annotated[str, Body], request: HttpRequest) -> HttpResponse:
        payload = json.loads(body)
        api_key = request.getHeaders().get("x-goog-api-key")
        self.state.record({"api": "gemini", "path": path, "apiKey": api_key, "body": payload})
        if api_key != "test-gemini-key":
            return HttpResponse.unauthorized()
        system = payload["systemInstruction"]["parts"][0]["text"]
        user = payload["contents"][0]["parts"][0]["text"]
        answer = json.dumps(self.state.answer(system, user))
        return HttpResponse.ok({
            "candidates": [{"content": {"role": "model", "parts": [{"text": answer}]}, "finishReason": "STOP"}],
            "modelVersion": path.split(":")[0],
        })

    @Post("/openai/v1/chat/completions")
    def openai_chat_completions(self, body: Annotated[str, Body], request: HttpRequest) -> HttpResponse:
        payload = json.loads(body)
        authorization = request.getHeaders().get("Authorization")
        self.state.record({"api": "openai", "authorization": authorization, "body": payload})
        messages = {m["role"]: m["content"] for m in payload["messages"]}
        answer = json.dumps(self.state.answer(messages["system"], messages["user"]))
        # Wrap in a markdown fence like some local models do
        return HttpResponse.ok({"choices": [{"index": 0, "message": {"role": "assistant", "content": f"```json\n{answer}\n```"}}]})

    @Get("/requests")
    def recorded_requests(self) -> list[dict[str, Any]]:
        return self.state.requests
