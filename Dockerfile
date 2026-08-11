FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts

RUN pip install --no-cache-dir .

# Local embeddings (fastembed / bge-small). Bake the model into the image so the
# bot embeds offline at runtime with no first-request download. FASTEMBED_CACHE_PATH
# is fixed so the runtime finds the same cache this build step populated (~130MB).
ENV FASTEMBED_CACHE_PATH=/app/.fastembed_cache
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

ENV OBSIDIAN_VAULT=/vault

CMD ["trellis"]
