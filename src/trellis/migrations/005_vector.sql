-- Vector search: semantic memory for the second brain. Adds a pgvector
-- embedding column to efforts and captures so related items can be found by
-- meaning (cosine similarity), not just keywords. Embeddings are generated and
-- kept current in Python; this migration only provisions storage and the index.

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE efforts  ADD COLUMN IF NOT EXISTS embedding vector(1536);
ALTER TABLE captures ADD COLUMN IF NOT EXISTS embedding vector(1536);

CREATE INDEX IF NOT EXISTS efforts_embedding_idx
    ON efforts USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS captures_embedding_idx
    ON captures USING hnsw (embedding vector_cosine_ops);
