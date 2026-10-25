"""S3-compatible object storage for artifacts and large payloads (SPEC 7.3, aioboto3)."""

# aioboto3 ships without type information.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from typing import Any, Protocol

import aioboto3

from orchestrix_core.config import Settings


class ObjectStore(Protocol):
    async def put(self, key: str, body: bytes, content_type: str) -> None: ...


class S3ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._session = aioboto3.Session()
        self._bucket = settings.s3_bucket
        self._client_args: dict[str, Any] = {
            "endpoint_url": settings.s3_endpoint,
            "aws_access_key_id": settings.s3_access_key.get_secret_value(),
            "aws_secret_access_key": settings.s3_secret_key.get_secret_value(),
            "region_name": "us-east-1",
        }

    async def put(self, key: str, body: bytes, content_type: str) -> None:
        client: Any = self._session.client("s3", **self._client_args)
        async with client as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=body, ContentType=content_type)
