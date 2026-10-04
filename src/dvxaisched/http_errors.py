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

"""Helpers for errors raised by awaited Micronaut HTTP client calls."""

from __future__ import annotations

from micronaut.http.client.exceptions import HttpClientResponseException


def java_cause(error: BaseException) -> object:
    """Returns the underlying Java exception of an awaited call.

    Awaiting a Java async result currently surfaces failures as
    ``micronaut_asyncio.MicronautJavaException`` that carries the original
    exception in ``java_exception``, rather than the Java exception itself.
    """
    return getattr(error, "java_exception", None) or error


def describe_http_error(error: BaseException) -> str:
    cause = java_cause(error)
    if isinstance(cause, HttpClientResponseException):
        return f"HTTP {cause.getStatus().getCode()}: {cause.getMessage()}"
    return str(error)
