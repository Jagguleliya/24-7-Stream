FROM python:3.11-slim

# Install FFmpeg, curl (for healthcheck), and DejaVu fonts (for drawtext overlays)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl fonts-dejavu-core fontconfig && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Pre-generate avatar frames and voice loop
RUN python avatar_engine.py || true

# Health check endpoint
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/health || exit 1

# Entry point
CMD ["python", "run.py", "--live"]
