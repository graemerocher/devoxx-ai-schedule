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

"""Provider-neutral LLM abstraction used by every agent.

Agents describe *what* they need (a task name, system and user prompts and a
JSON schema for the structured answer) and an ``LlmClient`` bean decides *how*
to obtain it. The active implementation is selected with the ``llm.provider``
property, so Gemini can be swapped for another backend (or a test double)
without touching the agents.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class LlmRequest:
    task: str
    """Stable identifier of the agent task, e.g. ``validate-interests`` or ``plan-day``."""
    system: str
    user: str
    schema: dict[str, Any]
    """JSON schema describing the structured response."""


class LlmException(Exception):
    """Raised when the LLM backend fails or returns an unusable response."""


class LlmClient(ABC):
    @abstractmethod
    async def generate_json(self, request: LlmRequest) -> dict[str, Any]:
        """Runs the request and returns the structured JSON response as a dict."""
        ...

    @abstractmethod
    def model_name(self) -> str:
        """Human-readable name of the backing model, reported by the health endpoint."""
        ...


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_json_object(text: str | None) -> dict[str, Any]:
    """Parses a model's text output as a JSON object, tolerating markdown fences."""
    if text is None or not text.strip():
        raise LlmException("LLM returned an empty response")
    match = _FENCE.match(text)
    if match:
        text = match.group(1)
    try:
        value = json.loads(text)
    except ValueError as e:
        raise LlmException(f"LLM returned invalid JSON: {e}") from e
    if not isinstance(value, dict):
        raise LlmException("LLM returned JSON that is not an object")
    return value


def clean_api_key(raw: str | None) -> str:
    """Normalizes an API key read from configuration or a mounted secret."""
    key = (raw or "").strip()
    # Secrets injected through some platforms arrive with stray closing braces.
    return key.rstrip("}").strip()


def mask_api_key(key: str) -> str:
    return f"...{key[-4:]}" if len(key) > 6 else "configured"
