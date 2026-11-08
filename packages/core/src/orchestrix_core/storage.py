"""S3-compatible object storage for artifacts and large payloads (SPEC 7.3, aioboto3).

The one store used by the API, the worker and mcp-files. Artifacts live at
`artifacts/{artifact_id}`; their name and type are on the `artifacts` row.
"""

# aioboto3 ships without type information.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from typing import Any, Protocol, Self

import aioboto3

from orchestrix_core.config import Settings


def artifact_key(artifact_id: str) -> str:
    return f"artifacts/{artifact_id}"


class ObjectStore(Protocol):
    async def put(self, key: str, body: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...


class S3ObjectStore:
    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str) -> None:
        self._session = aioboto3.Session()
        self._bucket = bucket
        self._client_args: dict[str, Any] = {
            "endpoint_url": endpoint,
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "region_name": "us-east-1",
        }

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            settings.s3_endpoint,
            settings.s3_bucket,
            settings.s3_access_key.get_secret_value(),
            settings.s3_secret_key.get_secret_value(),
        )

    def _client(self, **overrides: Any) -> Any:
        return self._session.client("s3", **{**self._client_args, **overrides})

    async def put(self, key: str, body: bytes, content_type: str) -> None:
        async with self._client() as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=body, ContentType=content_type)

    async def get(self, key: str) -> bytes:
        async with self._client() as s3:
            obj = await s3.get_object(Bucket=self._bucket, Key=key)
            return await obj["Body"].read()

    async def delete(self, key: str) -> None:
        async with self._client() as s3:
            await s3.delete_object(Bucket=self._bucket, Key=key)

    async def presign(self, key: str, *, public_endpoint: str, seconds: int = 300) -> str:
        """A short-lived GET URL (SPEC 15.4), signed for the endpoint browsers can reach."""
        async with self._client(endpoint_url=public_endpoint) as s3:
            return str(
                await s3.generate_presigned_url(
                    "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=seconds
                )
            )
