-- 001_init.sql — initial schema for the documents API.
--
-- This is intentionally the SAME table the capstone uses (roadmap page 20),
-- minus the `embedding vector(1536)` column. In Week 5 you'll add pgvector:
--     ALTER TABLE documents ADD COLUMN embedding vector(1536);
--     CREATE INDEX ON documents USING hnsw (embedding vector_cosine_ops);
-- so this Week 2 work is the literal foundation of the final system.
--
-- Run automatically by Postgres on first boot: docker-compose mounts this file
-- into /docker-entrypoint-initdb.d/, which the postgres image executes once
-- when the data volume is empty.

-- gen_random_uuid() lives in pgcrypto on older PGs; built-in from PG13+.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS documents (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- We list and sort by created_at DESC on every page load, so index it.
-- Without this, listing does a full table scan + in-memory sort.
CREATE INDEX IF NOT EXISTS idx_documents_created_at
    ON documents (created_at DESC);

-- GIN index lets you query inside the JSONB metadata efficiently, e.g.
--   WHERE metadata @> '{"source": "upload"}'
CREATE INDEX IF NOT EXISTS idx_documents_metadata
    ON documents USING gin (metadata);
