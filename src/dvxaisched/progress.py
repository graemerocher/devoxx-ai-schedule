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

"""Routes progress events from LangChain4j agent callbacks to the originating request.

Agent listeners and error handlers only see an ``AgenticScope``. The workflow
puts a request id into every scope's state (``requestId``) and registers the
request's progress listener here, so callbacks running on the parallel day
workers' threads can find it.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

from jakarta.inject import Singleton

from .models import WorkflowProgressEvent

ProgressListener = Callable[[WorkflowProgressEvent], None]

REQUEST_ID_KEY = "requestId"


@Singleton
class ProgressRegistry:
    def __init__(self):
        self._listeners: dict[str, ProgressListener] = {}
        self._lock = threading.Lock()

    def register(self, listener: ProgressListener) -> str:
        request_id = uuid.uuid4().hex
        with self._lock:
            self._listeners[request_id] = listener
        return request_id

    def unregister(self, request_id: str) -> None:
        with self._lock:
            self._listeners.pop(request_id, None)

    def emit(self, scope: Any, event: WorkflowProgressEvent) -> None:
        """Delivers the event to the request owning the agentic scope, if it is still listening."""
        if scope is None:
            return
        request_id = scope.readState(REQUEST_ID_KEY, "")
        with self._lock:
            listener = self._listeners.get(str(request_id)) if request_id else None
        if listener is not None:
            listener(event)
