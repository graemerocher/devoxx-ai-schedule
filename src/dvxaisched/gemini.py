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

"""Google Gemini backend for :class:`~dvxaisched.llm.LlmClient`.

Calls the Generative Language REST API (``models/{model}:generateContent``)
with structured JSON output through Micronaut's non-blocking HTTP client. The
API base URL is configurable with ``llm.gemini.base-url``.
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
from .llm import LlmClient, LlmException, LlmRequest, clean_api_key, mask_api_key, parse_json_object

JString = java.type("java.lang.String")

LOG = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"


@ConfigurationProperties("llm.gemini")
class GeminiConfig:
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    temperature: float = 0.2


def build_generate_content_body(request: LlmRequest, temperature: float) -> dict[str, Any]:
    return {
        "systemInstruction": {"parts": [{"text": request.system}]},
        "contents": [{"role": "user", "parts": [{"text": request.user}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseJsonSchema": request.schema,
        },
    }


def parse_generate_content_response(body: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except ValueError as e:
        raise LlmException(f"Gemini returned a non-JSON payload: {e}") from e
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates returned")
        raise LlmException(f"Gemini returned no candidates ({reason})")
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    return parse_json_object(text)


@Singleton
@Requires(property="llm.provider", value="gemini", defaultValue="gemini")
class GeminiLlmClient(LlmClient):
    def __init__(self, config: GeminiConfig, http: HttpClient):
        self.config = config
        self.http = http
        self.api_key = clean_api_key(config.api_key)
        self.model = (config.model or "").strip() or DEFAULT_MODEL
        self.base_url = (config.base_url or DEFAULT_BASE_URL).rstrip("/")

    @PostConstruct
    def log_configuration(self) -> None:
        if not self.api_key:
            LOG.warning("No GEMINI_API_KEY configured! Ensure the GEMINI_API_KEY environment variable is set.")
        else:
            LOG.info("Configured Gemini backend for model '%s' with API key (%s)", self.model, mask_api_key(self.api_key))

    def model_name(self) -> str:
        return self.model

    async def generate_json(self, request: LlmRequest) -> dict[str, Any]:
        if not self.api_key:
            raise LlmException("Gemini API key is not configured (set GEMINI_API_KEY)")
        body = build_generate_content_body(request, self.config.temperature)
        http_request = (
            HttpRequest.POST(f"{self.base_url}/v1beta/models/{self.model}:generateContent", json.dumps(body))
            .contentType(MediaType.APPLICATION_JSON)
            .accept(MediaType.APPLICATION_JSON)
            .header("x-goog-api-key", self.api_key)
        )
        try:
            response = await self.http.exchange(http_request, JString)
        except Exception as e:
            raise LlmException(f"Gemini request for task '{request.task}' failed: {describe_http_error(e)}") from e
        return parse_generate_content_response(str(response.body()))
