# Headless image for the pdfkb CLI (GUI needs a display; run app.py natively).
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data (kb.sqlite + chroma) persists in /data; mount a volume and/or point to
# an embedding server via PDFKB_OLLAMA_URL / PDFKB_EMBED_MODEL.
ENV PDFKB_DATA_DIR=/data
VOLUME ["/data"]

ENTRYPOINT ["python", "-m", "pdfkb.cli"]
CMD ["list"]
