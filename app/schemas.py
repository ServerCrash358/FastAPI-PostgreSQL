"""
schemas.py — pydantic models that define the API's data contract.

Separation of concerns:
  - These are *API* shapes (what JSON goes in and out), not DB rows.
  - Keeping request models (DocumentCreate) separate from response models
    (DocumentOut) means clients can't set server-controlled fields like
    `id` or `created_at`, and you can evolve the DB without breaking the API.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentCreate(BaseModel):
    """Request body for POST /documents. Only client-supplied fields appear here."""
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    # JSONB column — arbitrary structured metadata (source, tags, author …).
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentUpdate(BaseModel):
    """
    Request body for PATCH /documents/{id}.

    Every field is Optional so a client can send *only* what changes (partial
    update). We distinguish "field omitted" from "field set to null" via
    model_fields_set in the router.
    """
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, min_length=1)
    metadata: dict[str, Any] | None = None


class DocumentOut(BaseModel):
    """Response model. Includes server-controlled fields."""
    # from_attributes lets pydantic build this from objects with attributes;
    # asyncpg Records are dict-like, so we'll pass dict(record) — both work.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    content: str
    metadata: dict[str, Any]
    created_at: datetime


class DocumentList(BaseModel):
    """Paginated list envelope — total lets clients build page controls."""
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int


class HealthOut(BaseModel):
    status: str
    database: str
