"""Policy decisions (SPEC 15.1, FR-90): what OPA answers for a tool call."""

from typing import Literal

from pydantic import BaseModel

PolicyResult = Literal["allow", "deny", "require_approval"]


class PolicyDecision(BaseModel):
    result: PolicyResult
    reasons: list[str] = []

    @classmethod
    def deny(cls, reason: str) -> "PolicyDecision":
        return cls(result="deny", reasons=[reason])
