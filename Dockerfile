# Knowledgebases web app. Serves the Starlette app (UI + JSON API) on 8765.
#
# Two build args keep the image small by default and let it grow only as you need:
#   VECTOR_CLIENTS  client libraries for the vector profiles you use,
#                   e.g. "qdrant-client" or "qdrant-client weaviate-client pymilvus"
#   WITH_DOCLING    1 installs Docling into this image (heavy) for extraction without
#                   the separate docling-serve container
FROM python:3.11-slim

ARG WITH_DOCLING=0
ARG VECTOR_CLIENTS=""

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ -n "$VECTOR_CLIENTS" ]; then pip install --no-cache-dir $VECTOR_CLIENTS; fi \
 && if [ "$WITH_DOCLING" = "1" ]; then pip install --no-cache-dir docling; fi

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
USER kb

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/stats', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "pdfkb.web:app", "--host", "0.0.0.0", "--port", "8765"]
