"""Model catalogue for the UI's model pickers (amendment A4)."""

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from orchestrix_api.deps import Resources, User

router = APIRouter(prefix="/v1/models", tags=["models"])

HIDDEN = frozenset({"fallback-probe"})  # gateway-check only


class ModelInfo(BaseModel):
    id: str
    max_input_tokens: int | None = None
    supports_function_calling: bool | None = None
    supports_response_schema: bool | None = None
    default_for: list[str] = []


class ModelCatalogue(BaseModel):
    models: list[ModelInfo]
    defaults: dict[str, str]


@router.get("")
async def list_models(res: Resources, user: User) -> ModelCatalogue:
    """Chat models the gateway serves; embedding and rerank models are not selectable."""
    settings = res.settings
    try:
        response = await res.http.get(
            f"{settings.litellm_url}/model/info",
            headers={"Authorization": f"Bearer {settings.litellm_master_key.get_secret_value()}"},
        )
        response.raise_for_status()
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "model gateway unavailable"
        ) from exc
    defaults = {str(role): model for role, model in settings.default_models.items()}
    seen: dict[str, ModelInfo] = {}
    for entry in response.json().get("data", []):
        info: dict[str, Any] = entry.get("model_info") or {}
        name = str(entry.get("model_name", ""))
        if not name or name in HIDDEN or info.get("mode") != "chat" or name in seen:
            continue
        seen[name] = ModelInfo(
            id=name,
            max_input_tokens=info.get("max_input_tokens"),
            supports_function_calling=info.get("supports_function_calling"),
            supports_response_schema=info.get("supports_response_schema"),
            default_for=sorted(role for role, model in defaults.items() if model == name),
        )
    return ModelCatalogue(models=sorted(seen.values(), key=lambda m: m.id), defaults=defaults)
