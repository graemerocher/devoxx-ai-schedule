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

"""OpenAI-compatible Chat Completions backend for :class:`~dvxaisched.llm.LlmClient`.

Works with any server implementing ``POST {base}/chat/completions`` with
``response_format: json_schema`` - OpenAI itself, Ollama, vLLM, LM Studio and
others. Enable it with ``llm.provider = "openai"`` and point
``llm.openai.base-url`` at the server (e.g. ``http://localhost:11434/v1`` for Ollama).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import java
from jakarta.annotation import PostConstruct
from jakarta.inject import Singleton
from micronaut.context.annotation import ConfigurationProperties, Requires
from micronaut.http import HttpRequest, MediaType
from micronaut.http.client import HttpClient

from .http_errors import describe_http_error
from .llm import LlmClient, LlmException, LlmRequest, clean_api_key, parse_json_object

JString = java.type("java.lang.String")

LOG = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openai.com/v1"


@ConfigurationProperties("llm.openai")
class OpenAiConfig:
    api_key: str | None = None
    model: str | None = None
    base_url: str = DEFAULT_BASE_URL
    temperature: float = 0.2


def build_chat_completions_body(request: LlmRequest, model: str, temperature: float) -> dict[str, Any]:
    return {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": request.task.replace("-", "_"), "schema": request.schema},
        },
    }


def parse_chat_completions_response(body: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except ValueError as e:
        raise LlmException(f"Chat completions endpoint returned a non-JSON payload: {e}") from e
    choices = data.get("choices") or []
    if not choices:
        raise LlmException("Chat completions endpoint returned no choices")
    message = choices[0].get("message") or {}
    if message.get("refusal"):
        raise LlmException(f"Model refused the request: {message['refusal']}")
    return parse_json_object(message.get("content"))


@Singleton
@Requires(property="llm.provider", value="openai")
class OpenAiCompatibleLlmClient(LlmClient):
    def __init__(self, config: OpenAiConfig, http: HttpClient):
        self.config = config
        self.http = http
        self.api_key = clean_api_key(config.api_key)
        self.model = (config.model or "").strip()
        self.base_url = (config.base_url or DEFAULT_BASE_URL).rstrip("/")

    @PostConstruct
    def log_configuration(self) -> None:
        if not self.model:
            LOG.warning("llm.provider=openai but no llm.openai.model is configured (env: LLM_OPENAI_MODEL)")
        else:
            LOG.info("Configured OpenAI-compatible backend for model '%s' at %s", self.model, self.base_url)

    def model_name(self) -> str:
        return self.model or "unconfigured"

    async def generate_json(self, request: LlmRequest) -> dict[str, Any]:
        if not self.model:
            raise LlmException("llm.openai.model must be configured when llm.provider is 'openai'")
        body = build_chat_completions_body(request, self.model, self.config.temperature)
        http_request = (
            HttpRequest.POST(f"{self.base_url}/chat/completions", json.dumps(body))
            .contentType(MediaType.APPLICATION_JSON)
            .accept(MediaType.APPLICATION_JSON)
        )
        if self.api_key:
            http_request = http_request.bearerAuth(self.api_key)
        try:
            response = await self.http.exchange(http_request, JString)
        except Exception as e:
            raise LlmException(f"Chat completions request for task '{request.task}' failed: {describe_http_error(e)}") from e
        return parse_chat_completions_response(str(response.body()))
