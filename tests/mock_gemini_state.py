"""Requests recorded by the mock Gemini API (see ``mock_gemini_routes``).

The recorder is a Java ``StringBuffer`` bean (thread-safe, one JSON line per
request), so the pooled route module records into the same buffer from every
GraalPy context. A Java collection type would not do: Micronaut reads an
injection point of a collection type as "every bean of the element type".
"""

from __future__ import annotations

from jakarta.inject import Named, Singleton
from java.lang import StringBuffer
from micronaut.context.annotation import Factory, Requires

RECORDED_REQUESTS = "mock-gemini-requests"


@Factory
@Requires(property="mock-gemini.enabled", value="true")
class MockGeminiRequestsFactory:
    @Singleton
    @Named(RECORDED_REQUESTS)
    def recorded_requests(self) -> StringBuffer:
        return StringBuffer()
