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

"""In-memory cache of synthesized schedules keyed by normalized interests."""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

from jakarta.inject import Singleton
from micronaut.context.annotation import ConfigurationProperties

from .models import ScheduleResponse

LOG = logging.getLogger(__name__)

_WHITESPACE = re.compile(r"\s+")


def normalize_key(raw: str | None) -> str:
    """Lowercases, trims and collapses whitespace."""
    if raw is None:
        return ""
    return _WHITESPACE.sub(" ", raw.strip().lower())


@dataclass
class CacheEntry:
    response: ScheduleResponse
    created_at: float

    def is_expired(self, ttl_seconds: float, now: float) -> bool:
        return (now - self.created_at) > ttl_seconds


class ScheduleCache:
    def __init__(self, enabled: bool = True, ttl_minutes: int = 120, max_entries: int = 500):
        self.enabled = enabled
        self.ttl_seconds = max(1, ttl_minutes) * 60.0
        self.max_entries = max(10, max_entries)
        self._entries: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()

    def get(self, raw_interests: str | None) -> ScheduleResponse | None:
        if not self.enabled:
            return None
        key = normalize_key(raw_interests)
        if not key:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.is_expired(self.ttl_seconds, time.time()):
                del self._entries[key]
                return None
            return entry.response

    def put(self, raw_interests: str | None, response: ScheduleResponse | None) -> None:
        if not self.enabled or response is None or not response.valid:
            return
        key = normalize_key(raw_interests)
        if not key:
            return
        with self._lock:
            if len(self._entries) >= self.max_entries:
                self._evict_expired_or_oldest()
            self._entries[key] = CacheEntry(response, time.time())
            LOG.debug("Cached synthesized schedule for query: '%s' (cache size: %d)", key, len(self._entries))

    def _evict_expired_or_oldest(self) -> None:
        now = time.time()
        for key in [k for k, e in self._entries.items() if e.is_expired(self.ttl_seconds, now)]:
            del self._entries[key]
        if len(self._entries) >= self.max_entries:
            # Evict the oldest 20% of entries
            to_evict = max(1, self.max_entries // 5)
            oldest = sorted(self._entries.items(), key=lambda kv: kv[1].created_at)[:to_evict]
            for key, _ in oldest:
                del self._entries[key]

    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


@ConfigurationProperties("cache.schedule")
class ScheduleCacheConfig:
    enabled: bool = True
    ttl_minutes: int = 120
    max_entries: int = 500


@Singleton
class ScheduleCacheBean(ScheduleCache):
    def __init__(self, config: ScheduleCacheConfig):
        super().__init__(config.enabled, config.ttl_minutes, config.max_entries)
