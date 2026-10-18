"""Keep the Django admin reachable on the internal network only.

A request to ``/admin/`` is answered with 404 unless the peer address is in
``IDENTITY_ADMIN_ALLOWED_NETWORKS`` and no proxy header shows it was forwarded
(the gateway adds ``X-Forwarded-For``). The gateway must also never route
``/admin``; this check is the second line of defense.
"""

# mypy: disable-error-code="import-untyped,misc,no-any-return,no-untyped-call"

import ipaddress
from collections.abc import Callable

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse

_PROXY_HEADERS = ("HTTP_X_FORWARDED_FOR", "HTTP_FORWARDED", "HTTP_X_REAL_IP")


def _is_internal(request: HttpRequest) -> bool:
    if any(request.META.get(h) for h in _PROXY_HEADERS):
        return False
    try:
        peer = ipaddress.ip_address(request.META.get("REMOTE_ADDR", ""))
    except ValueError:
        return False
    return any(peer in ipaddress.ip_network(n) for n in settings.IDENTITY_ADMIN_ALLOWED_NETWORKS)


class InternalAdminOnlyMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path.startswith("/admin") and not _is_internal(request):
            raise Http404
        return self.get_response(request)
