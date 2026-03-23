# Dockerfile for Yandex Maps Profile Visitor

FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Set work directory
WORKDIR /app

# Install system dependencies (Playwright will install browser-specific deps via --with-deps)
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    procps \
    xvfb \
    x11vnc \
    fluxbox \
    wmctrl \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for better caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser with all OS dependencies
# Use shared path so it works for both root and appuser
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN playwright install --with-deps chromium

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p /app/logs /app/screenshots /app/data /app/browser_profiles /app/downloads

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]