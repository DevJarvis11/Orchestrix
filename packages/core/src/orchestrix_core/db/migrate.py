"""One-shot migration job: Alembic upgrade, then the LangGraph checkpointer tables (SPEC 21.2).

Run as `python -m orchestrix_core.db.migrate` from the repo root (where alembic.ini lives).
Both steps use the migrator role, so default privileges hand the new tables to orchestrix_app.
"""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from orchestrix_core.config import get_settings, libpq_dsn
from orchestrix_core.logging import configure_logging, get_logger

log = get_logger(__name__)


def alembic_config(url: str, ini_path: Path = Path("alembic.ini")) -> Config:
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


async def setup_checkpointer(dsn: str) -> None:
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        await saver.setup()


async def migrate(url: str, ini_path: Path = Path("alembic.ini")) -> None:
    # Alembic's async env.py calls asyncio.run(), so it needs a thread without a running loop.
    await asyncio.to_thread(command.upgrade, alembic_config(url, ini_path), "head")
    log.info("alembic upgrade complete")
    await setup_checkpointer(libpq_dsn(url))
    log.info("checkpointer setup complete")


def main() -> None:
    configure_logging()
    asyncio.run(migrate(get_settings().migrations_url))


if __name__ == "__main__":
    main()
