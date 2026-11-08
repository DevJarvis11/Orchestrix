"""Attachment uploads (FR-02, SPEC 17.1): one file per request, 10 MB, MIME sniffed from content."""

import hashlib
import uuid
from pathlib import PurePath
from typing import Final

from fastapi import APIRouter, HTTPException, UploadFile, status
from pydantic import BaseModel

from orchestrix_api.deps import Resources, User
from orchestrix_core.db.models import Artifact
from orchestrix_core.storage import artifact_key

router = APIRouter(prefix="/v1/uploads", tags=["uploads"])

MAX_BYTES: Final = 10 * 1024 * 1024
XLSX: Final = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TEXT_TYPES: Final = {".md": "text/markdown", ".txt": "text/plain", ".csv": "text/csv"}


class UploadAccepted(BaseModel):
    artifact_id: uuid.UUID
    name: str
    mime: str
    bytes: int


def sniff(body: bytes, filename: str) -> str | None:
    """Trust the bytes, not the client's Content-Type: pdf, xlsx, or UTF-8 md/txt/csv."""
    ext = PurePath(filename).suffix.lower()
    if body.startswith(b"%PDF-"):
        return "application/pdf" if ext == ".pdf" else None
    if body.startswith(b"PK\x03\x04"):
        return XLSX if ext == ".xlsx" and b"xl/" in body[:4096] else None
    if ext in TEXT_TYPES and b"\x00" not in body:
        try:
            body.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return TEXT_TYPES[ext]
    return None


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(file: UploadFile, res: Resources, user: User) -> UploadAccepted:
    name = PurePath(file.filename or "upload").name[:200]
    body = await file.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "attachments are limited to 10 MB")
    mime = sniff(body, name)
    if mime is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "allowed types: pdf, md, txt, csv, xlsx"
        )
    artifact_id = uuid.uuid4()
    await res.objects.put(artifact_key(str(artifact_id)), body, mime)
    async with res.sessions.begin() as s:
        s.add(
            Artifact(
                id=artifact_id,
                name=name,
                mime=mime,
                bytes=len(body),
                object_key=artifact_key(str(artifact_id)),
                sha256=hashlib.sha256(body).hexdigest(),
                uploaded_by=user.id,
            )
        )
    return UploadAccepted(artifact_id=artifact_id, name=name, mime=mime, bytes=len(body))
