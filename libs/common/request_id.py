"""Propagate a correlation identifier through every HTTP request.

The middleware keeps ``X-Request-ID`` when supplied and generates a UUID when
missing; it returns the value in the response and stores it in
``request.state``. It relates to the gateway and each service API, as well as
``core.outbox_event``, which stores the identifier in the event payload. It is
used to trace a request from the gateway through asynchronous workers.
"""

from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
