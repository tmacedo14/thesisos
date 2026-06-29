from __future__ import annotations

from typing import Mapping


ALLOWED_METHODS = "GET, HEAD, POST, OPTIONS"

TRANSITIONAL_CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "script-src 'self' 'unsafe-inline' https:",
        "style-src 'self' 'unsafe-inline' https:",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data: https:",
        "connect-src 'self' https: wss:",
        "media-src 'self' blob: https:",
        "frame-src 'self' https:",
        "worker-src 'self' blob:",
        "manifest-src 'self'",
        "upgrade-insecure-requests",
    ]
)

BASE_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        TRANSITIONAL_CONTENT_SECURITY_POLICY
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), "
        "payment=(), usb=(), browsing-topics=()"
    ),
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Permitted-Cross-Domain-Policies": "none",
}

HSTS_HEADER = "max-age=31536000"


def _forwarded_protocol(
    request_headers: Mapping[str, str] | None,
) -> str:
    if request_headers is None:
        return ""

    raw = request_headers.get("X-Forwarded-Proto", "")
    return str(raw).split(",", 1)[0].strip().lower()


def security_headers_for_request(
    request_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    headers = dict(BASE_SECURITY_HEADERS)

    if _forwarded_protocol(request_headers) == "https":
        headers["Strict-Transport-Security"] = HSTS_HEADER

    return headers
