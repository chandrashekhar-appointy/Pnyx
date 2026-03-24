FROM python:3.11-slim

# Install system dependencies: Redis, ffmpeg, supervisor
RUN apt-get update && apt-get install -y \
    curl \
    xz-utils \
    libpq-dev \
    gcc \
    libc++1 \
    redis-server \
    supervisor \
    && curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/ffmpeg.tar.xz \
    && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp \
    && cp /tmp/ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/ \
    && cp /tmp/ffmpeg-*-amd64-static/ffprobe /usr/local/bin/ \
    && rm -rf /tmp/ffmpeg* \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Setup Backend dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir gunicorn uvicorn && pip install --no-cache-dir -r requirements.txt

# Copy backend Python code
COPY app/ ./

# Copy supervisor configuration to manage all processes
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Ensure data and logs directories exist
RUN mkdir -p /app/data /app/logs /var/log/supervisor

# Setup environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Expose ports that might be accessed externally
# 5167: FastAPI Backend, 6379: Redis (internal, but exposed)
EXPOSE 5167 6379

# Start supervisor which will start Redis, FastAPI, and Celery
CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
