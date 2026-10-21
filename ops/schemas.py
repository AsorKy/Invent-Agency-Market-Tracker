"""Single source of truth for the database schema list.

The 7 canonical schemas of SPEC section 3.2 and ADR E2.1 live here, plus the
``validation`` schema that the ``core`` roles operate (REQ-LNS-010). Both the
migration guardian (``ops/check_migrations.py``) and the role bootstrap
(``ops/bootstrap_roles.py``) read this list, so adding a schema is one edit
here plus an expand migration. Relates to REQ-DAT-006, REQ-DAT-007, REQ-EXT-003.
"""

import re

SCHEMAS: tuple[str, ...] = ("identity", "ingest", "core", "pulse", "brief", "ml", "socio")

# Schemas that live in the same database but are operated by the roles of
# another schema: ``validation`` (ADR-IMT-0002 section 2.5) uses ``core_owner``
# and ``core_app``, so it adds no role. Key: schema, value: role base schema.
SHARED_ROLE_SCHEMAS: dict[str, str] = {"validation": "core"}

# Every schema the guardian accepts and the bootstrap creates.
CATALOG_SCHEMAS: tuple[str, ...] = (*SCHEMAS, *SHARED_ROLE_SCHEMAS)

# Schemas created by already applied, checksummed migrations that are retired
# by a contract migration or reconciled later. The guardian tolerates them.
LEGACY_SCHEMAS: frozenset[str] = frozenset({"analytics", "socioeconomic"})

EXTENSIONS: tuple[str, ...] = ("vector", "postgis", "pg_cron", "pg_trgm")

_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_identifier(name: str) -> str:
    """Return ``name`` when it is a safe lower-case SQL identifier, else raise."""
    if not _IDENT.fullmatch(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


def role_base(schema: str) -> str:
    """Return the schema whose roles operate ``schema`` (itself unless shared)."""
    return validate_identifier(SHARED_ROLE_SCHEMAS.get(schema, schema))


def owner_role(schema: str) -> str:
    """Return the migration owner role name of a schema."""
    return f"{role_base(schema)}_owner"


def app_role(schema: str) -> str:
    """Return the application runtime role name of a schema."""
    return f"{role_base(schema)}_app"
