# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies, ffmpeg, and postgres client libs
RUN apt-get update && apt-get install -y \
    curl \
    xz-utils \
    libpq-dev \
    gcc \
    libc++1 \
    && rm -rf /var/lib/apt/lists/* \
    && ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then FFMPEG_ARCH="amd64"; elif [ "$ARCH" = "aarch64" ]; then FFMPEG_ARCH="arm64"; else FFMPEG_ARCH="amd64"; fi && \
    curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-${FFMPEG_ARCH}-static.tar.xz -o /tmp/ffmpeg.tar.xz \
    && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp \
    && cp /tmp/ffmpeg-*-static/ffmpeg /usr/local/bin/ \
    && cp /tmp/ffmpeg-*-static/ffprobe /usr/local/bin/ \
    && rm -rf /tmp/ffmpeg* \
    && chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
# First, install CPU-only torch to save 2GB and speed up build
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Then install other requirements
RUN pip install --no-cache-dir gunicorn && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ .

# Create directory for database and logs
RUN mkdir -p /app/data /app/logs

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV DATABASE_PATH=/app/data/meeting_minutes.db
ENV DATABASE_URL=""

# Expose the port the app runs on
EXPOSE 5167

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:5167/health || exit 1

# Install gosu for safe user switching
RUN apt-get update && apt-get install -y gosu && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app

# Create entrypoint script to fix permissions at runtime
RUN echo '#!/bin/bash\n\
    # Fix permissions for mounted data directory\n\
    chown -R appuser:appuser /app/data 2>/dev/null || true\n\
    # Switch to appuser and run the application\n\
    exec gosu appuser "$@"' > /entrypoint.sh && chmod +x /entrypoint.sh

# Run the application via entrypoint
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5167"]
