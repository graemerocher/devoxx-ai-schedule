"""Test-only routes exposing the fake chat model's call statistics."""

from __future__ import annotations

from typing import Annotated, Any

from dev.langchain4j.model.chat import ChatModel
from jakarta.inject import Inject
from micronaut.context.annotation import Requires
from micronaut.http.annotation import Controller, Delete, Get

Controller("/test/fake-llm")
Requires(property="langchain4j.google-ai-gemini.enabled", value="false")

chat_model: Annotated[ChatModel, Inject]


def fake():
    # The bean is decorated by dvxaisched.structured_output.SchemaAwareChatModel
    return getattr(chat_model, "delegate", chat_model)


@Get("/stats")
def stats() -> dict[str, Any]:
    return fake().stats()


@Delete("/stats")
def reset() -> dict[str, bool]:
    fake().reset()
    return {"reset": True}
