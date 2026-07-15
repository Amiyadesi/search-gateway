from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class SearchResult(BaseModel):
    title: str
    url: HttpUrl | str
    snippet: str = ""
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
