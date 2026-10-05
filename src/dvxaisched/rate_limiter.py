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

"""Per-session and per-IP sliding window rate limiting.

``RateLimiterService`` is ``@ContextPooled``: every GraalPy context of the pool
has its own instance, so rate checks run in parallel. The sliding windows are
shared through Java concurrent maps (Caffeine caches managed by Micronaut
Cache), which every context sees, and each window is updated atomically with
``ConcurrentMap.compute``.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Annotated, Any

from jakarta.inject import Named, Singleton
from java.util import ArrayList
from java.util.concurrent import ConcurrentHashMap, Semaphore
from micronaut.cache import SyncCache
from micronaut.context.annotation import ConfigurationProperties, Factory
from micronaut.context.python.scope import ContextPooled

LOG = logging.getLogger(__name__)

MAX_CONCURRENT_WORKFLOWS = 10
WORKFLOW_LIMITER = "workflow-limiter"
SESSION_WINDOWS_CACHE = "ratelimit-sessions"
IP_WINDOWS_CACHE = "ratelimit-ips"


@dataclass
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0
    reason: str = ""

    @staticmethod
    def allow() -> RateLimitDecision:
        return RateLimitDecision(True)

    @staticmethod
    def reject(retry_after_seconds: int, reason: str) -> RateLimitDecision:
        return RateLimitDecision(False, max(1, retry_after_seconds), reason)


def try_acquire(windows: Any, key: str, limit: int, window_seconds: float, now: float) -> int:
    """Records a request in the sliding window of ``key`` if it is under ``limit``.

    The window is a Java list of request timestamps replaced atomically with
    ``ConcurrentMap.compute``. Returns 0 when the request is allowed, otherwise
    the number of seconds until the oldest request leaves the window.
    """
    retry_after = 0

    def remap(_key, timestamps):
        nonlocal retry_after
        recent = [t for t in (timestamps or []) if now - t < window_seconds]
        if len(recent) < limit:
            recent.append(now)
            retry_after = 0
        else:
            retry_after = max(1, math.ceil(window_seconds - (now - min(recent))))
        # A Java list, so the window is readable from every GraalPy context
        return ArrayList(recent)

    windows.compute(key, remap)
    return retry_after


class RateLimiter:
    def __init__(
        self,
        enabled: bool = True,
        session_limit: int = 5,
        session_window_seconds: int = 60,
        ip_limit: int = 60,
        ip_window_seconds: int = 60,
        session_windows: Any | None = None,
        ip_windows: Any | None = None,
    ):
        self.enabled = enabled
        self.session_limit = max(1, session_limit)
        self.session_window = float(max(1, session_window_seconds))
        self.ip_limit = max(1, ip_limit)
        self.ip_window = float(max(1, ip_window_seconds))
        self.session_windows = session_windows if session_windows is not None else ConcurrentHashMap()
        self.ip_windows = ip_windows if ip_windows is not None else ConcurrentHashMap()

    def check_rate_limit(self, session_id: str | None, client_ip: str | None) -> RateLimitDecision:
        if not self.enabled:
            return RateLimitDecision.allow()
        now = time.time()

        # 1. Session limit first, so a throttled session does not burn shared IP aggregate tokens
        effective_session = session_id if session_id and session_id.strip() else client_ip
        if effective_session and effective_session.strip():
            retry_after = try_acquire(self.session_windows, effective_session, self.session_limit, self.session_window, now)
            if retry_after:
                LOG.warning("Session rate limit exceeded for %s: retry in %ds", effective_session, retry_after)
                return RateLimitDecision.reject(
                    retry_after,
                    f"Rate limit reached (max {self.session_limit} requests/minute). Please wait {retry_after} seconds.",
                )

        # 2. IP aggregate limit (protects against a single IP generating thousands of fake sessions)
        if client_ip and client_ip.strip():
            retry_after = try_acquire(self.ip_windows, client_ip, self.ip_limit, self.ip_window, now)
            if retry_after:
                LOG.warning("IP aggregate rate limit exceeded for %s: retry in %ds", client_ip, retry_after)
                return RateLimitDecision.reject(
                    retry_after,
                    f"Too many requests from this network. Please wait {retry_after} seconds.",
                )
        return RateLimitDecision.allow()

    def reset(self) -> None:
        self.session_windows.clear()
        self.ip_windows.clear()


@ConfigurationProperties("ratelimit")
class RateLimitConfig:
    enabled: bool = True
    session_limit: int = 5
    session_window_seconds: int = 60
    ip_limit: int = 60
    ip_window_seconds: int = 60


@ContextPooled
class RateLimiterService(RateLimiter):
    """The rate limiter of the application; idle windows expire with their cache entries."""

    def __init__(
        self,
        config: RateLimitConfig,
        session_windows: Annotated[SyncCache, Named(SESSION_WINDOWS_CACHE)],
        ip_windows: Annotated[SyncCache, Named(IP_WINDOWS_CACHE)],
    ):
        super().__init__(
            config.enabled,
            config.session_limit,
            config.session_window_seconds,
            config.ip_limit,
            config.ip_window_seconds,
            session_windows.getNativeCache().asMap(),
            ip_windows.getNativeCache().asMap(),
        )


@Factory
class WorkflowLimiterFactory:
    @Singleton
    @Named(WORKFLOW_LIMITER)
    def workflow_limiter(self) -> Semaphore:
        """Bounds the schedule workflows running at once.

        Exposed as a Java ``Semaphore`` bean so that pooled route modules in every
        GraalPy context share the same permits.
        """
        return Semaphore(MAX_CONCURRENT_WORKFLOWS)
