from typing import Any
from pydantic import BaseModel


class FracFocusListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[dict[str, Any]]
