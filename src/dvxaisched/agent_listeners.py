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

"""Customization of the LangChain4j agent builders through Micronaut lifecycle listeners.

* agents get an ``AgentListener`` that streams progress (per-day completion and
  tool calls) to the originating request through the ``ProgressRegistry``;
* the parallel day mapper runs its sub-agents on Micronaut's IO executor (GraalPy
  cannot run Python callbacks on virtual threads) and retries a failing day
  through an ``errorHandler`` without discarding the other days.
"""

from __future__ import annotations

import logging
from typing import Annotated

from dev.langchain4j.agentic.agent import AgentBuilder, ErrorRecoveryResult
from dev.langchain4j.agentic.observability import AgentListener
from dev.langchain4j.agentic.workflow import ParallelMapperService
from jakarta.inject import Named, Singleton
from java.util.concurrent import ExecutorService
from micronaut.context.event import BeanCreatedEvent, BeanCreatedEventListener
from micronaut.scheduling import TaskExecutors

from .models import WorkflowProgressEvent
from .progress import ProgressRegistry

LOG = logging.getLogger(__name__)

MAX_DAY_RETRIES = 2
AGENT2 = "Agent 2: Schedule Optimizer"
PARALLEL_AGENT = "Parallel Day Optimizer"
DAY_AGENT_PREFIX = "build_day_schedule"


class ProgressAgentListener(AgentListener):
    """Observability hooks shared by all agents."""

    def __init__(self, progress: ProgressRegistry):
        self.progress = progress

    def beforeAgentInvocation(self, request) -> None:
        LOG.info("[AgentListener.beforeAgentInvocation] Agent '%s' starting", request.agentName())

    def afterAgentInvocation(self, response) -> None:
        agent_name = str(response.agentName() or "")
        LOG.info("[AgentListener.afterAgentInvocation] Agent '%s' finished", agent_name)
        if agent_name.startswith(DAY_AGENT_PREFIX):
            self.progress.emit(response.agenticScope(), WorkflowProgressEvent(
                "agent2_progress", PARALLEL_AGENT, "Curated day schedule concurrently"
            ))

    def beforeAgentToolExecution(self, tool) -> None:
        execution = tool.toolExecution()
        tool_name = execution.request().name() if execution is not None and execution.request() is not None else "tool"
        LOG.info("[AgentListener.beforeAgentToolExecution] Tool: %s", tool_name)
        self.progress.emit(tool.agenticScope(), WorkflowProgressEvent(
            "tool_call", AGENT2, f"Searching session catalog via tool '{tool_name}'..."
        ))

    def afterAgentToolExecution(self, tool) -> None:
        LOG.info("[AgentListener.afterAgentToolExecution] Tool finished")
        self.progress.emit(tool.agenticScope(), WorkflowProgressEvent(
            "agent2_start", AGENT2, "Catalog search completed. Synthesizing schedule..."
        ))


@Singleton
class AgentBuilderCustomizer(BeanCreatedEventListener[AgentBuilder]):
    """Attaches the progress listener to every declarative agent, including the day sub-agents."""

    def __init__(self, progress: ProgressRegistry):
        self.listener = ProgressAgentListener(progress)

    def onCreated(self, event: BeanCreatedEvent[AgentBuilder]) -> AgentBuilder:
        builder = event.getBean()
        builder.listener(self.listener)
        return builder


@Singleton
class ParallelMapperCustomizer(BeanCreatedEventListener[ParallelMapperService]):
    """Configures the executor and error handling of the parallel day mapper."""

    def __init__(self, executor: Annotated[ExecutorService, Named(TaskExecutors.IO)], progress: ProgressRegistry):
        self.executor = executor
        self.progress = progress

    def onCreated(self, event: BeanCreatedEvent[ParallelMapperService]) -> ParallelMapperService:
        builder = event.getBean()
        builder.executor(self.executor)
        builder.errorHandler(self.retry_failed_day)
        return builder

    def retry_failed_day(self, error_context) -> object:
        """Retries a failed day worker up to ``MAX_DAY_RETRIES`` times without discarding the other days."""
        agent_name = str(error_context.agentName() or "dayAgent")
        exception = error_context.exception()
        message = exception.getMessage() if exception is not None else "unknown error"
        scope = error_context.agenticScope()

        retry_key = f"retry_count_{agent_name}"
        retry_count = scope.readState(retry_key, 0)
        if retry_count < MAX_DAY_RETRIES:
            scope.writeState(retry_key, retry_count + 1)
            LOG.warning(
                "Error in agent '%s' (attempt %d of %d): %s. Retrying via LangChain4j errorHandler...",
                agent_name, retry_count + 1, MAX_DAY_RETRIES, message,
            )
            self.progress.emit(scope, WorkflowProgressEvent(
                "agent2_progress", PARALLEL_AGENT,
                f"Transient issue on day worker ({agent_name}). Retrying (attempt {retry_count + 1})...",
            ))
            return ErrorRecoveryResult.retry()

        LOG.error("Agent '%s' exceeded max retries: %s", agent_name, message)
        return ErrorRecoveryResult.throwException()
