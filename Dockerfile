FROM python:3.9-slim

# Install system dependencies
RUN apt-get update -y && \
    apt-get install -y \
    python3-tk \
    tk-dev \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python packages
RUN pip install --no-cache-dir pdf2image Pillow

# Copy application files
COPY pdfcomp.py thepdffile.py wido.py ./

# Run the application
CMD ["python", "pdfcomp.py"]
