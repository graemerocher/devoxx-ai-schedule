"""Mock of the Gemini ``generateContent`` REST endpoint, served by the application under test.

Enabled with ``mock-gemini.enabled=true``; the real Micronaut LangChain4j Gemini
``ChatModel`` is then pointed at it (``langchain4j.google-ai-gemini.chat-model.base-url``)
so the provider wiring and structured-output parsing are tested over HTTP
without network access.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from jakarta.inject import Inject
from micronaut.context.annotation import Requires
from micronaut.http import HttpRequest, HttpResponse
from micronaut.http.annotation import Body, Controller, Get, Post

from fake_chat_model import answer, task_for
from mock_gemini_state import MockGeminiRequests

Controller("/mock-gemini")
Requires(property="mock-gemini.enabled", value="true")

recorded: Annotated[MockGeminiRequests, Inject]


def _text(content: dict[str, Any] | None) -> str:
    return "".join(p.get("text", "") for p in (content or {}).get("parts", []))


@Post("/v1beta/models/{+path}")
def generate_content(path: str, body: Annotated[str, Body], request: HttpRequest) -> HttpResponse:
    payload = json.loads(body)
    system = _text(payload.get("systemInstruction"))
    user = "\n".join(_text(c) for c in payload.get("contents", []) if c.get("role") == "user")
    task = task_for(system)
    recorded.record({
        "path": path,
        "apiKey": request.getHeaders().get("x-goog-api-key"),
        "task": task,
        "generationConfig": payload.get("generationConfig", {}),
    })
    text = json.dumps(answer(task, user, []))
    return HttpResponse.ok({
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
    })


@Get("/requests")
def requests() -> list[dict[str, Any]]:
    return recorded.requests
