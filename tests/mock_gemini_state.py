"""Requests recorded by the mock Gemini API (see ``mock_gemini_routes``)."""

from __future__ import annotations

import threading
from typing import Any

from jakarta.inject import Singleton
from micronaut.context.annotation import Requires


@Singleton
@Requires(property="mock-gemini.enabled", value="true")
class MockGeminiRequests:
    def __init__(self):
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.requests.append(entry)
