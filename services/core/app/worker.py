"""Reserve the Core entrypoint for future commercial jobs.

The current process sleeps as a placeholder so ``docker-compose.yml`` can keep
the command when lead and outcome jobs are introduced. It will relate to
``services/core/app/main.py``, ``libs/domain/pipeline.py``, and future CRM jobs.
It fixes the extension point in F0 without inventing production logic.
"""

import asyncio

from libs.common.settings import get_settings


async def main() -> None:
    settings = get_settings()
    while True:
        await asyncio.sleep(settings.worker_failure_sleep_seconds)


if __name__ == "__main__":
    asyncio.run(main())
