"""Role and permission matrix for the five identity roles (ARCH-0001 E1.1).

Pure data and helpers with no Django imports, so the gateway and other
services can reuse the matrix. Permissions are coarse capability strings.
"""

ROLES: tuple[str, ...] = ("admin", "strategist", "intake", "bd_rep", "analyst")

PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset(
        {
            "tenant.configure",
            "user.manage",
            "import.run",
            "scoring.approve",
            "pulse.view",
            "lead.edit",
            "lead.approve",
            "report.export",
            "budget.manage",
            "pipeline.move",
        }
    ),
    "strategist": frozenset({"pulse.view", "project.formulate", "lead.approve"}),
    "intake": frozenset({"inbox.manage", "lead.curate", "lead.qualify", "lead.edit"}),
    "bd_rep": frozenset(
        {"lead.contact", "activity.log", "enrichment.request", "pipeline.move", "lead.edit"}
    ),
    "analyst": frozenset({"read.all", "report.export", "budget.manage", "pulse.view"}),
}

# Roles whose data access is limited to assigned cities and service lines.
SCOPED_ROLES: frozenset[str] = frozenset({"intake", "bd_rep"})


def permissions_for(role: str) -> frozenset[str]:
    """Return the permissions of ``role``; unknown roles get none (fail closed)."""
    return PERMISSIONS.get(role, frozenset())


def has_permission(role: str, permission: str) -> bool:
    """Return whether ``role`` grants ``permission``."""
    return permission in permissions_for(role)


def scope_allows(role: str, scopes: list[tuple[str, str]], city: str, service_line: str) -> bool:
    """Return whether a membership may see data for ``city`` and ``service_line``.

    Unscoped roles see everything in the tenant. Scoped roles need a matching
    ``(city, service_line)`` pair; an empty string in a pair means "any".
    """
    if role not in SCOPED_ROLES:
        return role in PERMISSIONS
    return any(c in ("", city) and s in ("", service_line) for c, s in scopes)
