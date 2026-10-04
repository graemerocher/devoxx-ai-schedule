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

"""Per-session and per-IP sliding window rate limiting."""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass

from jakarta.inject import Singleton
from micronaut.context.annotation import ConfigurationProperties

LOG = logging.getLogger(__name__)


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


class SlidingWindow:
    """Thread-safe O(1) circular buffer sliding-window rate limiter."""

    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window_seconds = window_seconds
        self.timestamps = [0.0] * limit
        self.head = 0
        self.count = 0
        self.last_access = time.time()
        self._lock = threading.Lock()

    def try_acquire(self, now: float) -> RateLimitDecision:
        with self._lock:
            self.last_access = now
            if self.count < self.limit:
                self.timestamps[self.count] = now
                self.count += 1
                return RateLimitDecision.allow()

            # The oldest request in the current circular window is at index `head`
            elapsed = now - self.timestamps[self.head]
            if elapsed < self.window_seconds:
                return RateLimitDecision.reject(math.ceil(self.window_seconds - elapsed), "Limit exceeded")

            # Window has slid: overwrite the oldest slot and advance head
            self.timestamps[self.head] = now
            self.head = (self.head + 1) % self.limit
            return RateLimitDecision.allow()

    def is_stale(self, now: float, max_idle_seconds: float) -> bool:
        with self._lock:
            return (now - self.last_access) > max_idle_seconds


class RateLimiter:
    def __init__(
        self,
        enabled: bool = True,
        session_limit: int = 5,
        session_window_seconds: int = 60,
        ip_limit: int = 60,
        ip_window_seconds: int = 60,
    ):
        self.enabled = enabled
        self.session_limit = max(1, session_limit)
        self.session_window = float(max(1, session_window_seconds))
        self.ip_limit = max(1, ip_limit)
        self.ip_window = float(max(1, ip_window_seconds))
        self._session_windows: dict[str, SlidingWindow] = {}
        self._ip_windows: dict[str, SlidingWindow] = {}
        self._lock = threading.Lock()

    def _window(self, windows: dict[str, SlidingWindow], key: str, limit: int, window: float) -> SlidingWindow:
        with self._lock:
            win = windows.get(key)
            if win is None:
                win = windows[key] = SlidingWindow(limit, window)
            return win

    def check_rate_limit(self, session_id: str | None, client_ip: str | None) -> RateLimitDecision:
        if not self.enabled:
            return RateLimitDecision.allow()
        now = time.time()

        # 1. Session limit first, so a throttled session does not burn shared IP aggregate tokens
        effective_session = session_id if session_id and session_id.strip() else client_ip
        if effective_session and effective_session.strip():
            decision = self._window(
                self._session_windows, effective_session, self.session_limit, self.session_window
            ).try_acquire(now)
            if not decision.allowed:
                LOG.warning("Session rate limit exceeded for %s: retry in %ds", effective_session, decision.retry_after_seconds)
                return RateLimitDecision.reject(
                    decision.retry_after_seconds,
                    f"Rate limit reached (max {self.session_limit} requests/minute). "
                    f"Please wait {decision.retry_after_seconds} seconds.",
                )

        # 2. IP aggregate limit (protects against a single IP generating thousands of fake sessions)
        if client_ip and client_ip.strip():
            decision = self._window(self._ip_windows, client_ip, self.ip_limit, self.ip_window).try_acquire(now)
            if not decision.allowed:
                LOG.warning("IP aggregate rate limit exceeded for %s: retry in %ds", client_ip, decision.retry_after_seconds)
                return RateLimitDecision.reject(
                    decision.retry_after_seconds,
                    f"Too many requests from this network. Please wait {decision.retry_after_seconds} seconds.",
                )

        # Periodically clean up stale records to prevent memory growth
        if len(self._session_windows) > 2000:
            self._prune(self._session_windows, self.session_window * 3, now)
        if len(self._ip_windows) > 2000:
            self._prune(self._ip_windows, self.ip_window * 3, now)
        return RateLimitDecision.allow()

    def _prune(self, windows: dict[str, SlidingWindow], max_idle: float, now: float) -> None:
        with self._lock:
            for key in [k for k, w in windows.items() if w.is_stale(now, max_idle)]:
                del windows[key]

    def reset(self) -> None:
        with self._lock:
            self._session_windows.clear()
            self._ip_windows.clear()


@ConfigurationProperties("ratelimit")
class RateLimitConfig:
    enabled: bool = True
    session_limit: int = 5
    session_window_seconds: int = 60
    ip_limit: int = 60
    ip_window_seconds: int = 60


@Singleton
class RateLimiterService(RateLimiter):
    def __init__(self, config: RateLimitConfig):
        super().__init__(
            config.enabled,
            config.session_limit,
            config.session_window_seconds,
            config.ip_limit,
            config.ip_window_seconds,
        )
