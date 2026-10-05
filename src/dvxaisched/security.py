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

"""HTTP security response headers applied to every route and static resource."""

from __future__ import annotations

from micronaut.http import MutableHttpResponse
from micronaut.http.annotation import ResponseFilter, ServerFilter

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "frame-ancestors 'none';"
)


@ServerFilter("/**")
class SecurityHeadersFilter:
    @ResponseFilter
    def add_security_headers(self, response: MutableHttpResponse) -> None:
        response.header("X-Content-Type-Options", "nosniff")
        response.header("X-Frame-Options", "DENY")
        response.header("Referrer-Policy", "strict-origin-when-cross-origin")
        response.header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
