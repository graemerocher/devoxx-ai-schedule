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

"""Caching of synthesized schedules with Micronaut Cache.

The ``schedules`` cache (Caffeine, configured under ``micronaut.caches.schedules``)
is keyed by the normalized interests, so case and whitespace variations of a
query share one entry. Only valid schedules are cached: a rejected schedule is
raised as ``RejectedSchedule``, and failed invocations are never cached.
"""

from __future__ import annotations

import re
from typing import Annotated

from jakarta.inject import Named, Singleton
from java.lang import Object
from micronaut.cache import SyncCache
from micronaut.cache.annotation import Cacheable, CacheConfig

from .models import ScheduleResponse
from .workflow import DevoxxAgentWorkflowService, ProgressListener

SCHEDULES_CACHE = "schedules"

_WHITESPACE = re.compile(r"\s+")


def normalize_key(raw: str | None) -> str:
    """Lowercases, trims and collapses whitespace."""
    if raw is None:
        return ""
    return _WHITESPACE.sub(" ", raw.strip().lower())


class RejectedSchedule(Exception):
    """Carries a rejected (invalid) schedule out of a cached method so it is not cached."""

    def __init__(self, response: ScheduleResponse):
        super().__init__(response.validation_message)
        self.response = response


def rejected_from_error(error: BaseException) -> ScheduleResponse | None:
    """Returns the rejected schedule carried by ``error``, if it is (or wraps) a ``RejectedSchedule``.

    Exceptions raised by a coroutine behind a Micronaut AOP proxy (here the
    ``@Cacheable`` interceptor) currently come back wrapped as
    ``CompletionException`` -> ``PolyglotException`` rather than as the original
    Python exception, so the cause chain is searched for the guest exception.
    """
    current: object | None = getattr(error, "java_exception", None) or error
    for _ in range(10):
        if current is None:
            return None
        if isinstance(current, RejectedSchedule):
            return current.response
        if hasattr(current, "isGuestException") and current.isGuestException():
            current = current.getGuestObject()
            continue
        current = current.getCause() if hasattr(current, "getCause") else getattr(current, "__cause__", None)
    return None


@Singleton
@CacheConfig(SCHEDULES_CACHE)
class ScheduleCache:
    def __init__(
        self,
        workflow_service: DevoxxAgentWorkflowService,
        cache: Annotated[SyncCache, Named(SCHEDULES_CACHE)],
    ):
        self.workflow_service = workflow_service
        self.cache = cache

    @Cacheable(parameters=["key"])
    async def schedule(self, key: str, interests: str, progress: ProgressListener | None) -> ScheduleResponse:
        """Synthesizes the schedule for ``interests``, cached under its normalized ``key``.

        ``progress`` receives the workflow's progress events on a cache miss.
        """
        response = await self.workflow_service.process_schedule_request(interests, progress)
        if not response.valid:
            raise RejectedSchedule(response)
        return response

    def get(self, key: str) -> ScheduleResponse | None:
        """Returns the cached schedule for a normalized key without synthesizing one."""
        if not key:
            return None
        return self.cache.get(key, Object).orElse(None)
