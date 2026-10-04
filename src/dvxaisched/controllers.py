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

"""HTTP API of the schedule curator."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Annotated

from dev.langchain4j.model.chat import ChatModel
from java.net import URI
from java.util.concurrent import Semaphore
from micronaut.http import HttpRequest, HttpResponse, HttpStatus, MediaType
from micronaut.http.annotation import Body, Controller, Get, Post, QueryValue
from micronaut.http.sse import Event
from org.reactivestreams import Publisher
from reactor.core.publisher import Flux, Sinks

from .agent_listeners import AGENT2
from .cache import ScheduleCache, normalize_key, rejected_from_error
from .client_ip import resolve_client_ip, resolve_session_id
from .conference import DevoxxConferenceService
from .models import (
    GUARDRAIL_AGENT,
    ConferenceTalk,
    ScheduleRequest,
    TalkAlternativeRequest,
    WorkflowProgressEvent,
    empty_alternatives,
    progress_complete,
    progress_rejected,
    rejected_schedule,
)
from .rate_limiter import RateLimitDecision, RateLimiterService
from .workflow import MAX_INTERESTS_LENGTH, DevoxxAgentWorkflowService, sanitize_for_log

LOG = logging.getLogger(__name__)

MAX_CONCURRENT_WORKFLOWS = 10
BUSY_MESSAGE = (
    "The scheduling service is currently busy handling maximum concurrent requests. Please retry in a few moments."
)
TOO_LONG_MESSAGE = f"Input exceeds maximum allowed length of {MAX_INTERESTS_LENGTH} characters."

SAMPLE_INTERESTS: list[dict[str, str]] = [
    {
        "title": "AI Agents & GenAI",
        "badge": "Agentic",
        "prompt": "I'm interested in AI agents, Generative AI, LLM evaluation, LangChain4j, and loop engineering.",
    },
    {
        "title": "Java Language & JVM",
        "badge": "Java",
        "prompt": "A schedule focused on the Java language, latest features, Project Loom virtual threads, Valhalla, Amber, and best practices.",
    },
    {
        "title": "Mind the Geek & Quirky",
        "badge": "Geeky",
        "prompt": "A geeky agenda with fun, entertaining, surprising topics like robotics, Raspberry Pi, game engines, and space computing.",
    },
    {
        "title": "Architecture & Modernization",
        "badge": "Architecture",
        "prompt": "Modern enterprise architecture, modular monoliths, distributed systems, event-driven design, and guardrails.",
    },
    {
        "title": "Cloud Native & Kubernetes",
        "badge": "Cloud",
        "prompt": "Cloud-native Java, Kubernetes platforms, container hardening, vector databases, and GPU infrastructure.",
    },
]


def _rate_limit_message(decision: RateLimitDecision) -> str:
    return f"Rate limit exceeded. {decision.reason} Please retry in {decision.retry_after_seconds} seconds."


@Controller
class HomeController:
    @Get("/")
    def index(self) -> HttpResponse:
        return HttpResponse.redirect(URI.create("/index.html"))


@Controller("/api")
class ScheduleController:
    def __init__(
        self,
        workflow_service: DevoxxAgentWorkflowService,
        conference_service: DevoxxConferenceService,
        rate_limiter: RateLimiterService,
        schedule_cache: ScheduleCache,
        chat_model: ChatModel,
    ):
        self.workflow_service = workflow_service
        self.conference_service = conference_service
        self.rate_limiter = rate_limiter
        self.schedule_cache = schedule_cache
        self.model_name = self._model_name(chat_model)
        # A Java semaphore is safe to share between the Netty event loops and
        # the GraalPy contexts serving concurrent requests.
        self.workflow_limiter = Semaphore(MAX_CONCURRENT_WORKFLOWS)

    @Post(uri="/schedule", consumes=MediaType.APPLICATION_JSON, produces=MediaType.APPLICATION_JSON)
    async def generate_schedule(self, request: Annotated[ScheduleRequest, Body], http_request: HttpRequest) -> HttpResponse:
        if request is None or not request.interests or not request.interests.strip():
            return HttpResponse.badRequest(rejected_schedule("Interests cannot be empty."))
        if len(request.interests) > MAX_INTERESTS_LENGTH:
            return HttpResponse.badRequest(rejected_schedule(TOO_LONG_MESSAGE))

        interests = request.interests.strip()
        LOG.info("Received schedule generation request: '%s'", sanitize_for_log(interests))

        # 1. Check rate limit (per session and IP aggregate)
        session_id = resolve_session_id(http_request)
        client_ip = resolve_client_ip(http_request)
        decision = self.rate_limiter.check_rate_limit(session_id, client_ip)
        if not decision.allowed:
            LOG.warning("Rate limit rejected schedule request for IP %s (session %s): %s", client_ip, session_id, decision.reason)
            return (
                HttpResponse.status(HttpStatus.TOO_MANY_REQUESTS)
                .header("Retry-After", str(decision.retry_after_seconds))
                .body(rejected_schedule(_rate_limit_message(decision)))
            )

        # 2. Check the schedule cache
        cache_key = normalize_key(interests)
        cached = self.schedule_cache.get(cache_key)
        if cached is not None:
            LOG.info("Serving schedule from cache for query: '%s'", sanitize_for_log(interests))
            return HttpResponse.ok(cached)

        # 3. Concurrency limiter & workflow execution
        if not self.workflow_limiter.tryAcquire():
            LOG.warning("Concurrently running schedule workflows limit reached (%d); rejecting request", MAX_CONCURRENT_WORKFLOWS)
            return HttpResponse.status(HttpStatus.SERVICE_UNAVAILABLE).body(rejected_schedule(BUSY_MESSAGE))
        try:
            return HttpResponse.ok(await self.schedule_cache.schedule(cache_key, interests, None))
        except Exception as e:
            rejected = rejected_from_error(e)
            if rejected is None:
                raise
            return HttpResponse.ok(rejected)
        finally:
            self.workflow_limiter.release()

    @Get(uri="/schedule/stream", produces=MediaType.TEXT_EVENT_STREAM)
    async def stream_schedule(
        self,
        interests: Annotated[str, QueryValue(defaultValue="")],
        http_request: HttpRequest,
    ) -> Publisher:
        if not interests or not interests.strip():
            return Flux.just(Event.of(progress_rejected("Interests cannot be empty.", 0)))
        if len(interests) > MAX_INTERESTS_LENGTH:
            return Flux.just(Event.of(progress_rejected(TOO_LONG_MESSAGE, 0)))

        clean_interests = interests.strip()
        LOG.info("Received streaming schedule request: '%s'", sanitize_for_log(clean_interests))

        # 1. Check rate limit (per session and IP aggregate)
        session_id = resolve_session_id(http_request)
        client_ip = resolve_client_ip(http_request)
        decision = self.rate_limiter.check_rate_limit(session_id, client_ip)
        if not decision.allowed:
            LOG.warning("Rate limit rejected streaming request for IP %s (session %s): %s", client_ip, session_id, decision.reason)
            return Flux.just(Event.of(progress_rejected(_rate_limit_message(decision), 0)))

        # 2. Check the schedule cache
        cache_key = normalize_key(clean_interests)
        cached = self.schedule_cache.get(cache_key)
        if cached is not None:
            LOG.info("Serving streaming schedule from cache for query: '%s'", sanitize_for_log(clean_interests))
            return Flux.just(
                Event.of(WorkflowProgressEvent("agent1_done", GUARDRAIL_AGENT, f"Query verified (cache hit): {clean_interests}", 5)),
                Event.of(WorkflowProgressEvent("agent2_done", AGENT2, "Loaded personalized timetable from cache.", 10)),
                Event.of(progress_complete(cached, 15)),
            )

        # 3. Concurrency limiter & streaming execution on the request's event loop
        if not self.workflow_limiter.tryAcquire():
            LOG.warning("Concurrently running schedule workflows limit reached (%d); rejecting request", MAX_CONCURRENT_WORKFLOWS)
            return Flux.just(Event.of(progress_rejected(BUSY_MESSAGE, 0)))

        sink = Sinks.many().unicast().onBackpressureBuffer()
        # Progress events also arrive from the parallel day workers' threads;
        # Reactor sinks require serialized emissions.
        emit_lock = threading.Lock()

        def emit(event: WorkflowProgressEvent) -> None:
            with emit_lock:
                sink.tryEmitNext(Event.of(event))

        async def run_workflow() -> None:
            try:
                # Progress (including the rejection event) is streamed by the workflow itself
                await self.schedule_cache.schedule(cache_key, clean_interests, emit)
            except asyncio.CancelledError:
                LOG.info("Streaming schedule request cancelled by client")
                raise
            except Exception as e:
                if rejected_from_error(e) is not None:
                    return
                LOG.error("Error streaming schedule: %s", e)
                emit(progress_rejected(
                    "An unexpected error occurred while curating the schedule. Please try again with different topics.", 0
                ))
            finally:
                self.workflow_limiter.release()
                with emit_lock:
                    sink.tryEmitComplete()

        loop = asyncio.get_running_loop()
        task = loop.create_task(run_workflow())
        return sink.asFlux().doOnCancel(lambda: loop.call_soon_threadsafe(task.cancel))

    @Get(uri="/tracks", produces=MediaType.APPLICATION_JSON)
    def get_tracks(self) -> list[str]:
        return self.conference_service.get_all_tracks()

    @Get(uri="/talks", produces=MediaType.APPLICATION_JSON)
    def get_talks(
        self,
        q: Annotated[str, QueryValue(defaultValue="")],
        day: Annotated[str, QueryValue(defaultValue="")],
        limit: Annotated[int, QueryValue(defaultValue="20")],
    ) -> list[ConferenceTalk]:
        safe_limit = max(1, min(limit, 100))
        return self.conference_service.search_talks(q, day, safe_limit)

    @Get(uri="/talks/{id}", produces=MediaType.APPLICATION_JSON)
    def get_talk_by_id(self, id: int) -> ConferenceTalk | None:
        return self.conference_service.get_talk_by_id(id)

    @Post(uri="/schedule/alternatives", consumes=MediaType.APPLICATION_JSON, produces=MediaType.APPLICATION_JSON)
    async def get_alternatives(
        self, request: Annotated[TalkAlternativeRequest, Body], http_request: HttpRequest
    ) -> HttpResponse:
        if request is None or request.talk_id <= 0:
            return HttpResponse.badRequest(empty_alternatives(0, "Invalid talk ID."))

        client_ip = resolve_client_ip(http_request)
        session_id = resolve_session_id(http_request)
        decision = self.rate_limiter.check_rate_limit(session_id, client_ip)
        if not decision.allowed:
            LOG.warning("Rate limit rejected alternatives request for IP %s (session %s): %s", client_ip, session_id, decision.reason)
            return (
                HttpResponse.status(HttpStatus.TOO_MANY_REQUESTS)
                .header("Retry-After", str(decision.retry_after_seconds))
                .body(empty_alternatives(request.talk_id, _rate_limit_message(decision)))
            )

        LOG.info("Finding alternatives for talk ID %d with interests: '%s'", request.talk_id, sanitize_for_log(request.interests))
        return HttpResponse.ok(await self.workflow_service.find_alternatives(request.interests, request.talk_id))

    @Get(uri="/sample-interests", produces=MediaType.APPLICATION_JSON)
    def get_sample_interests(self) -> list[dict[str, str]]:
        return SAMPLE_INTERESTS

    @Get(uri="/health", produces=MediaType.APPLICATION_JSON)
    def health(self) -> dict[str, object]:
        return {
            "status": "UP",
            "conference": "Devoxx Belgium 2026",
            "dates": "October 5-9, 2026",
            "venue": "Kinepolis, Antwerp",
            "totalTalksLoaded": len(self.conference_service.talks),
            "model": self.model_name,
        }

    @staticmethod
    def _model_name(chat_model: ChatModel) -> str:
        try:
            name = chat_model.defaultRequestParameters().modelName()
            if name:
                return str(name)
        except Exception:
            pass
        return "unknown"
