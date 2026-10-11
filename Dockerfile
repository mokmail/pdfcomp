# pdfkb web app. Serves the Starlette app (UI + JSON API) on 8765.
FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Knowledge base + vector store + settings live here; mount a volume.
ENV PDFKB_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8765

CMD ["python", "-m", "uvicorn", "pdfkb.web:app", "--host", "0.0.0.0", "--port", "8765"]
