"""Shared fakes for the Brief unit tests: Redis, core source, and summarizers."""

from uuid import UUID, uuid4

from services.brief.app.core_source import CoreNotFound, CoreUnavailable
from services.brief.app.models import BriefRules, CompanyFacts

COMPANY = uuid4()
TENANT = uuid4()


class FakeRedis:
    def __init__(self) -> None:
        self.locks: dict[str, str] = {}
        self.set_calls: list[tuple[str, int]] = []

    async def set(self, name: str, value: str, *, nx: bool, px: int) -> bool:
        self.set_calls.append((name, px))
        if nx and name in self.locks:
            return False
        self.locks[name] = value
        return True

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int:
        key, owner = keys_and_args
        if self.locks.get(key) == owner:
            del self.locks[key]
            return 1
        return 0


class FakeCore:
    def __init__(self, facts: CompanyFacts | None = None) -> None:
        self.facts = facts or CompanyFacts(company_id=COMPANY, version=1)
        self.rules = BriefRules()
        self.config_version = 1
        self.down = False
        self.missing = False

    async def get_facts(self, company_id: UUID, tenant_id: UUID | None) -> CompanyFacts:
        if self.down:
            raise CoreUnavailable("down")
        if self.missing:
            raise CoreNotFound("missing")
        return self.facts.model_copy(update={"company_id": company_id})

    async def get_rules(self, tenant_id: UUID | None) -> tuple[BriefRules, int]:
        if self.down:
            raise CoreUnavailable("down")
        return self.rules, self.config_version
