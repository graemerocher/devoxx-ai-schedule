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

"""Resolution of the real client IP and anonymous session token of a request."""

from __future__ import annotations

import re

from micronaut.http import HttpRequest

_NON_IP_CHARS = re.compile(r"[^a-fA-F0-9.:]")
_NON_TOKEN_CHARS = re.compile(r"[^a-zA-Z0-9_-]")

LOCALHOST = "127.0.0.1"


def _sanitize_ip(ip: str) -> str:
    """Supports IPv4 and IPv6 and removes a port if present."""
    clean = ip.strip()
    if clean.startswith("[") and "]" in clean:
        clean = clean[1:clean.index("]")]
    elif clean.count(":") == 1:
        # IPv4 with port (e.g. 1.2.3.4:5678)
        clean = clean[:clean.index(":")]
    return _NON_IP_CHARS.sub("", clean)


def _sanitize_token(raw: str) -> str:
    clean = _NON_TOKEN_CHARS.sub("", raw.strip())[:64]
    return clean or "anonymous"


def client_ip_from(x_forwarded_for: str | None, x_real_ip: str | None, remote_address: str | None) -> str:
    """Cloud Run appends the verified connecting IP as the last X-Forwarded-For entry."""
    if x_forwarded_for and x_forwarded_for.strip():
        last = x_forwarded_for.split(",")[-1].strip()
        if last:
            return _sanitize_ip(last)
    if x_real_ip and x_real_ip.strip():
        return _sanitize_ip(x_real_ip)
    return remote_address or LOCALHOST


def session_id_from(session_header: str | None, session_param: str | None, client_ip: str) -> str:
    """Prefers the X-Session-ID header, then the sessionId query parameter, then the client IP."""
    if session_header and session_header.strip():
        return _sanitize_token(session_header)
    if session_param and session_param.strip():
        return _sanitize_token(session_param)
    return client_ip


def _remote_address(request: HttpRequest) -> str | None:
    try:
        address = request.getRemoteAddress()
        if address is not None and address.getAddress() is not None:
            return str(address.getAddress().getHostAddress())
    except Exception:
        pass
    return None


def resolve_client_ip(request: HttpRequest | None) -> str:
    if request is None:
        return LOCALHOST
    headers = request.getHeaders()
    return client_ip_from(headers.get("X-Forwarded-For"), headers.get("X-Real-IP"), _remote_address(request))


def resolve_session_id(request: HttpRequest | None) -> str:
    if request is None:
        return "anonymous"
    return session_id_from(
        request.getHeaders().get("X-Session-ID"),
        request.getParameters().get("sessionId"),
        resolve_client_ip(request),
    )
