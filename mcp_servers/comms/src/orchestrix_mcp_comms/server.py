"""mcp-comms: comms.send_email, simulated (SPEC 12.3). Writes the outbox; never sends."""

from typing import Annotated, Literal

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, EmailStr, Field, SecretStr
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from orchestrix_core.db.models import EmailOutbox
from orchestrix_tools.conventions import idempotent_id, new_server, run_id_of
from orchestrix_tools.server_settings import ServerSettings


class CommsSettings(ServerSettings):
    database_url: SecretStr  # app role, SQLAlchemy URL


class EmailReceipt(BaseModel):
    message_id: str
    status: Literal["simulated"]


def build_server(
    settings: CommsSettings, sessions: async_sessionmaker[AsyncSession] | None = None
) -> FastMCP:
    mcp = new_server("comms", "Simulated email: messages go to the outbox and are never delivered.")
    db = sessions or async_sessionmaker(
        create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True),
        expire_on_commit=False,
    )

    @mcp.tool(
        name="send_email", description="Send an email (simulated). Sensitive: needs approval."
    )
    async def send_email(  # pyright: ignore[reportUnusedFunction]
        to: Annotated[list[EmailStr], Field(min_length=1, max_length=10)],
        subject: Annotated[str, Field(min_length=1, max_length=300)],
        body_md: Annotated[str, Field(min_length=1, max_length=50_000)],
        ctx: Context,  # pyright: ignore[reportMissingTypeArgument]
    ) -> EmailReceipt:
        run_id = run_id_of(ctx)
        message_id = idempotent_id(ctx)  # a replayed call lands on the same outbox row
        async with db.begin() as s:
            await s.execute(
                insert(EmailOutbox)
                .values(
                    id=message_id,
                    run_id=run_id,
                    to_addrs=[str(a) for a in to],
                    subject=subject,
                    body_md=body_md,
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
        return EmailReceipt(message_id=str(message_id), status="simulated")

    return mcp
