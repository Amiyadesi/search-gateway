from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, model_validator


class AnySearchOptions(BaseModel):
    tag: str | None = Field(default=None, max_length=120)
    domain: str | None = Field(default=None, max_length=40)
    sub_domain: str | None = Field(default=None, max_length=120)
    params: dict[str, Any] = Field(default_factory=dict)
    sub_domain_params: dict[str, Any] = Field(default_factory=dict)
    zone: str | None = Field(default=None, max_length=20)
    language: str | None = Field(default=None, max_length=35)

    @model_validator(mode="after")
    def normalize(self) -> "AnySearchOptions":
        tag = (self.tag or self.sub_domain or "").strip() or None
        if self.tag and self.sub_domain and self.tag.strip() != self.sub_domain.strip():
            raise ValueError("AnySearch tag 与 sub_domain 必须一致")
        if self.domain and not tag:
            raise ValueError("AnySearch domain 需要同时提供 tag/sub_domain")
        if self.domain and tag and tag.split(".", 1)[0] != self.domain.strip():
            raise ValueError("AnySearch domain 必须匹配 tag/sub_domain 前缀")
        params = self.params or self.sub_domain_params or {}
        if not isinstance(params, dict) or len(params) > 50:
            raise ValueError("AnySearch params 必须是最多 50 个字段的 object")
        self.tag = tag
        self.params = params
        self.zone = self.zone.strip() if self.zone else None
        self.language = self.language.strip() if self.language else None
        return self

    def provider_kwargs(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "tag": self.tag,
                "params": self.params or None,
                "zone": self.zone,
                "language": self.language,
            }.items()
            if value is not None
        }


class AnySearchBatchItem(AnySearchOptions):
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=10, ge=1, le=10)


class AnySearchBatchRequest(BaseModel):
    queries: list[AnySearchBatchItem] = Field(min_length=1, max_length=5)


def parse_anysearch_params(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("AnySearch params 必须是 JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("AnySearch params 必须是 JSON object")
    return value
