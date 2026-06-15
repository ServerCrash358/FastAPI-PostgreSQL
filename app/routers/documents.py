"""
documents.py — CRUD for the `documents` table, using raw asyncpg + Redis cache.

Patterns shown here:
  - Parameterised SQL ($1, $2 …) — NEVER string-format user input into SQL.
    asyncpg sends the query and the args separately, so SQL injection is
    impossible by construction.
  - Read-through cache on GET-by-id; cache invalidation on update/delete.
  - Correct HTTP status codes (201 Created, 204 No Content, 404, 409).
  - Pagination via LIMIT/OFFSET with a total count.
"""

from __future__ import annotations

import json
import uuid

import asyncpg
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.cache import cache_delete, cache_get, cache_set, doc_key
from app.db import get_conn
from app.schemas import DocumentCreate, DocumentList, DocumentOut, DocumentUpdate

router = APIRouter(prefix="/documents", tags=["documents"])


def get_redis(request: Request) -> aioredis.Redis:
    """Dependency: pull the shared Redis client off app.state."""
    return request.app.state.redis


# ── CREATE ─────────────────────────────────────────────────────────────────
@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def create_document(
    body: DocumentCreate,
    conn: asyncpg.Connection = Depends(get_conn),
) -> DocumentOut:
    # RETURNING gives us the DB-generated id + created_at in a single round trip,
    # instead of INSERT then a second SELECT.
    row = await conn.fetchrow(
        """
        INSERT INTO documents (title, content, metadata)
        VALUES ($1, $2, $3::jsonb)
        RETURNING id, title, content, metadata, created_at
        """,
        body.title,
        body.content,
        json.dumps(body.metadata),   # asyncpg wants jsonb as a JSON string
    )
    return _to_out(row)


# ── LIST ─────────────────────────────────────────────────────────────────
@router.get("", response_model=DocumentList)
async def list_documents(
    conn: asyncpg.Connection = Depends(get_conn),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> DocumentList:
    # Two queries: one page of rows + one total count.
    # count(*) OVER() could fuse them, but a separate COUNT is clearer for now.
    rows = await conn.fetch(
        """
        SELECT id, title, content, metadata, created_at
        FROM documents
        ORDER BY created_at DESC
        LIMIT $1 OFFSET $2
        """,
        limit,
        offset,
    )
    total = await conn.fetchval("SELECT count(*) FROM documents")
    return DocumentList(
        items=[_to_out(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── READ (cached) ──────────────────────────────────────────────────────────
@router.get("/{doc_id}", response_model=DocumentOut)
async def get_document(
    doc_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
    redis: aioredis.Redis = Depends(get_redis),
) -> DocumentOut:
    key = doc_key(str(doc_id))

    # 1. Try the cache first.
    cached = await cache_get(redis, key)
    if cached is not None:
        return DocumentOut.model_validate_json(cached)

    # 2. Miss → hit Postgres.
    row = await conn.fetchrow(
        """
        SELECT id, title, content, metadata, created_at
        FROM documents WHERE id = $1
        """,
        doc_id,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Document not found")

    out = _to_out(row)
    # 3. Populate the cache for next time (store the serialised response).
    await cache_set(redis, key, out.model_dump_json())
    return out


# ── UPDATE (partial) ─────────────────────────────────────────────────────
@router.patch("/{doc_id}", response_model=DocumentOut)
async def update_document(
    doc_id: uuid.UUID,
    body: DocumentUpdate,
    conn: asyncpg.Connection = Depends(get_conn),
    redis: aioredis.Redis = Depends(get_redis),
) -> DocumentOut:
    # model_fields_set tells us which fields the client actually sent, so a
    # PATCH that omits `content` doesn't overwrite it with null.
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No fields to update")

    # Build "col = $n" assignments dynamically but safely: column names are
    # from our own allow-list (the schema), values go through placeholders.
    set_clauses: list[str] = []
    values: list[object] = []
    for i, (col, val) in enumerate(fields.items(), start=1):
        if col == "metadata":
            set_clauses.append(f"{col} = ${i}::jsonb")
            values.append(json.dumps(val))
        else:
            set_clauses.append(f"{col} = ${i}")
            values.append(val)
    values.append(doc_id)  # last placeholder is the WHERE id

    row = await conn.fetchrow(
        f"""
        UPDATE documents SET {", ".join(set_clauses)}
        WHERE id = ${len(values)}
        RETURNING id, title, content, metadata, created_at
        """,
        *values,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Document not found")

    await cache_delete(redis, doc_key(str(doc_id)))  # invalidate stale cache
    return _to_out(row)


# ── DELETE ─────────────────────────────────────────────────────────────────
@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_conn),
    redis: aioredis.Redis = Depends(get_redis),
) -> None:
    # execute() returns a status string like "DELETE 1"; the trailing number
    # is the affected row count — we use it to detect a missing id.
    result = await conn.execute("DELETE FROM documents WHERE id = $1", doc_id)
    if result.endswith("0"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Document not found")
    await cache_delete(redis, doc_key(str(doc_id)))


# ── helpers ─────────────────────────────────────────────────────────────
def _to_out(row: asyncpg.Record) -> DocumentOut:
    """
    asyncpg returns JSONB columns as a raw string; decode it back to a dict
    before handing the row to pydantic.
    """
    data = dict(row)
    if isinstance(data.get("metadata"), str):
        data["metadata"] = json.loads(data["metadata"])
    return DocumentOut.model_validate(data)
