FROM python:3.11-slim

# System dependencies required by docling
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download docling models at build time (not runtime)
RUN python -c "from docling.document_converter import DocumentConverter; DocumentConverter()"

# Copy server code
COPY server.py .

EXPOSE 8000

CMD ["python", "server.py", "http"]
