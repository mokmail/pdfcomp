# Knowledgebases — one image, several variants. Pick the smallest one that does your job.
#
#   docker build -t knowledgebases .                      # = slim (embedded stores only)
#   docker build --target clients  -t knowledgebases .   # + every self-hosted vector client
#   docker build --target docling  -t knowledgebases .   # + in-process Docling  (heavy)
#   docker build --target full     -t knowledgebases .   # + both
#   docker build --target custom   -t knowledgebases \   # + exactly what you name
#     --build-arg VECTOR_CLIENTS="qdrant-client" --build-arg WITH_DOCLING=1
#
# | target  | what it adds                                          | roughly |
# |---------|-------------------------------------------------------|---------|
# | slim    | nothing — pymupdf + chromadb + the web app            | ~1.0 GB |
# | clients | qdrant, opensearch/elasticsearch, weaviate, milvus,   | ~1.3 GB |
# |         | pgvector, redis, typesense and mongodb clients        |         |
# | docling | Docling (torch and friends) for in-process extraction | ~5 GB   |
# | full    | clients + docling                                     | ~5.5 GB |
# | custom  | whatever VECTOR_CLIENTS / WITH_DOCLING name           | varies  |
#
# If Docling runs as its own container (`docker compose --profile docling`), this image
# needs none of the docling target — point the app at the service with PDFKB_DOCLING_URL.
# docker-compose.yml picks the target through APP_VARIANT, and ./bin/kb picks it for you.

FROM python:3.11-slim AS base

ARG VECTOR_CLIENTS=""
ARG WITH_DOCLING=0

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Knowledge base + vector store + settings live here; mount a volume.
ENV PDFKB_DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
VOLUME ["/data"]
EXPOSE 8765

# Run as an ordinary user. A fresh named volume inherits this directory's ownership,
# so a mounted /data stays writable without any chown dance on the host.
RUN useradd -m -u 10001 kb && mkdir -p /data && chown -R kb:kb /data /app

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/stats', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "pdfkb.web:app", "--host", "0.0.0.0", "--port", "8765"]


# --------------------------------------------------------------- variants
# A variant that cannot import the app is worse than no variant, so every stage below ends
# with the same two steps: put the app's own requirements back in charge (a client library
# must never quietly move starlette/uvicorn underneath it), then prove the import works —
# the build fails here instead of the container crash-looping later.

FROM base AS slim
USER kb
RUN python -c "import pdfkb.web; print('slim: app imports OK')"


# Every supported vector-database client, baked in so nothing is ever pip-installed at
# runtime (a container would lose that the moment it is recreated).
FROM base AS clients
USER root
RUN pip install --no-cache-dir \
      qdrant-client opensearch-py weaviate-client pymilvus "psycopg[binary]" \
      redis typesense pymongo \
 && pip install --no-cache-dir -r requirements.txt \
 && python -c "import pdfkb.web, qdrant_client, opensearchpy, weaviate, pymilvus, psycopg, redis, typesense, pymongo; print('clients: app + every vector client imports OK')"
USER kb


# Docling inside this image instead of a separate container. Heavy: it pulls torch.
FROM base AS docling
USER root
RUN pip install --no-cache-dir docling \
 && pip install --no-cache-dir -r requirements.txt \
 && python -c "import pdfkb.web, docling; print('docling: app + docling import OK')"
USER kb


# Everything, for a machine that would rather not think about it.
FROM base AS full
USER root
RUN pip install --no-cache-dir \
      qdrant-client opensearch-py weaviate-client pymilvus "psycopg[binary]" \
      redis typesense pymongo docling \
 && pip install --no-cache-dir -r requirements.txt \
 && python -c "import pdfkb.web, qdrant_client, opensearchpy, weaviate, pymilvus, psycopg, redis, typesense, pymongo, docling; print('full: app + clients + docling import OK')"
USER kb


# Exactly what you name, and nothing else.
FROM base AS custom
USER root
RUN if [ -n "$VECTOR_CLIENTS" ]; then pip install --no-cache-dir $VECTOR_CLIENTS; fi \
 && if [ "$WITH_DOCLING" = "1" ]; then pip install --no-cache-dir docling; fi \
 && pip install --no-cache-dir -r requirements.txt \
 && python -c "import pdfkb.web; print('custom: app imports OK')"
USER kb


# Keep this last: `docker build .` with no --target would otherwise build whichever stage
# was written last, and it should be the lean one.
FROM slim AS default
