"""Single source of truth for the database schema list.

The 7 canonical schemas of SPEC section 3.2 and ADR E2.1 live here. Both the
migration guardian (``ops/check_migrations.py``) and the role bootstrap
(``ops/bootstrap_roles.py``) read this list, so adding a schema is one edit
here plus an expand migration. Relates to REQ-DAT-006, REQ-DAT-007, REQ-EXT-003.
"""

import re

SCHEMAS: tuple[str, ...] = ("identity", "ingest", "core", "pulse", "brief", "ml", "socio")

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


def owner_role(schema: str) -> str:
    """Return the migration owner role name of a schema."""
    return f"{validate_identifier(schema)}_owner"


def app_role(schema: str) -> str:
    """Return the application runtime role name of a schema."""
    return f"{validate_identifier(schema)}_app"
