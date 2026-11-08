"""mcp-files: files.read_artifact and files.write_artifact, backed by S3 (SPEC 12.3)."""

import base64
import binascii
import csv
import hashlib
import io
from typing import Annotated
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from orchestrix_core.db.models import Artifact, Run
from orchestrix_core.storage import ObjectStore, S3ObjectStore, artifact_key
from orchestrix_tools.conventions import idempotent_id, new_server, run_id_of, tool_error
from orchestrix_tools.server_settings import ServerSettings

MAX_WRITE_BYTES = 10 * 1024 * 1024
TEXT_MIMES = ("text/", "application/json", "application/xml")


class FilesSettings(ServerSettings):
    database_url: SecretStr  # app role, SQLAlchemy URL
    s3_endpoint: str = "http://s3:9000"
    s3_bucket: str = "orchestrix"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")


class ArtifactText(BaseModel):
    artifact_id: str
    name: str
    mime: str
    text: str
    truncated: bool


class WrittenArtifact(BaseModel):
    artifact_id: str


def to_text(body: bytes, name: str, mime: str) -> str:
    """A readable preview: text as-is, spreadsheets as CSV, PDFs as extracted text."""
    lower = name.lower()
    if lower.endswith(".xlsx") or "spreadsheetml" in mime:
        from openpyxl import load_workbook

        sheet = load_workbook(io.BytesIO(body), read_only=True, data_only=True).active
        out = io.StringIO()
        writer = csv.writer(out)
        for row in sheet.iter_rows(values_only=True) if sheet is not None else []:
            writer.writerow(["" if v is None else v for v in row])
        return out.getvalue()
    if lower.endswith(".pdf") or mime == "application/pdf":
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(body)).pages)
    if mime.startswith(TEXT_MIMES) or lower.endswith((".md", ".txt", ".csv", ".tsv", ".json")):
        return body.decode("utf-8", errors="replace")
    raise ValueError(f"no text preview for {mime}")


def build_server(
    settings: FilesSettings,
    objects: ObjectStore | None = None,
    sessions: async_sessionmaker[AsyncSession] | None = None,
) -> FastMCP:
    mcp = new_server("files", "Read the run's attachments and artifacts; store new files.")
    store = objects or S3ObjectStore(
        settings.s3_endpoint,
        settings.s3_bucket,
        settings.s3_access_key.get_secret_value(),
        settings.s3_secret_key.get_secret_value(),
    )
    db = sessions or async_sessionmaker(
        create_async_engine(settings.database_url.get_secret_value(), pool_pre_ping=True),
        expire_on_commit=False,
    )

    @mcp.tool(
        name="read_artifact", description="Read an attachment or artifact of this run as text."
    )
    async def read_artifact(  # pyright: ignore[reportUnusedFunction]
        artifact_id: Annotated[str, Field(min_length=1, max_length=64)],
        ctx: Context,  # pyright: ignore[reportMissingTypeArgument]
        max_chars: Annotated[int, Field(ge=100, le=100_000)] = 20_000,
    ) -> ArtifactText:
        run_id = run_id_of(ctx)
        try:
            wanted = UUID(artifact_id)
        except ValueError as exc:
            raise tool_error("artifact_not_found", f"no artifact {artifact_id}") from exc
        async with db() as s:
            task_id = (
                await s.execute(select(Run.task_id).where(Run.id == run_id))
            ).scalar_one_or_none()
            if task_id is None:  # unknown run: never fall into `task_id IS NULL`
                raise tool_error("artifact_not_found", f"no artifact {artifact_id} for this run")
            artifact = (
                await s.execute(
                    select(Artifact).where(
                        Artifact.id == wanted,
                        or_(Artifact.run_id == run_id, Artifact.task_id == task_id),
                    )
                )
            ).scalar_one_or_none()
        if artifact is None:  # unknown, or another run's: same answer, no existence leak
            raise tool_error("artifact_not_found", f"no artifact {artifact_id} for this run")
        body = await store.get(artifact.object_key)  # deliverables live outside artifacts/
        try:
            text = to_text(body, artifact.name, artifact.mime)
        except ValueError as exc:
            raise tool_error("unsupported_type", str(exc)) from exc
        return ArtifactText(
            artifact_id=artifact_id,
            name=artifact.name,
            mime=artifact.mime,
            text=text[:max_chars],
            truncated=len(text) > max_chars,
        )

    @mcp.tool(
        name="write_artifact", description="Store a file produced by an agent; returns its id."
    )
    async def write_artifact(  # pyright: ignore[reportUnusedFunction]
        name: Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^/\\]+$")],
        mime: Annotated[str, Field(min_length=3, max_length=100)],
        content_b64: Annotated[str, Field(min_length=1, max_length=14_000_000)],
        ctx: Context,  # pyright: ignore[reportMissingTypeArgument]
    ) -> WrittenArtifact:
        run_id = run_id_of(ctx)
        try:
            body = base64.b64decode(content_b64, validate=True)
        except binascii.Error as exc:
            raise tool_error("bad_content", "content_b64 is not valid base64") from exc
        if len(body) > MAX_WRITE_BYTES:
            raise tool_error("too_large", "files are limited to 10 MB")
        artifact_id = idempotent_id(ctx)  # a replayed call rewrites the same artifact
        await store.put(artifact_key(str(artifact_id)), body, mime)
        async with db.begin() as s:
            task_id = (
                await s.execute(select(Run.task_id).where(Run.id == run_id))
            ).scalar_one_or_none()
            if task_id is None:
                raise tool_error("missing_run", "unknown run")
            await s.execute(
                insert(Artifact)
                .values(
                    id=artifact_id,
                    run_id=run_id,
                    task_id=task_id,
                    name=name,
                    mime=mime,
                    bytes=len(body),
                    object_key=artifact_key(str(artifact_id)),
                    sha256=hashlib.sha256(body).hexdigest(),
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
        return WrittenArtifact(artifact_id=str(artifact_id))

    return mcp
