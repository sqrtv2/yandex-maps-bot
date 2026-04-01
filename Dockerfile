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
    fontconfig \
    cabextract \
    && rm -rf /var/lib/apt/lists/*

# Install Microsoft core fonts (Arial, Times New Roman, Verdana, etc.)
# These are needed to match Windows font fingerprint
RUN echo "deb http://deb.debian.org/debian bookworm contrib" >> /etc/apt/sources.list && \
    apt-get update && \
    echo "ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true" | debconf-set-selections && \
    apt-get install -y --no-install-recommends ttf-mscorefonts-installer && \
    fc-cache -f -v && \
    rm -rf /var/lib/apt/lists/*

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

# Make entrypoint executable
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

USER appuser

# Xvfb virtual display for headed Chrome
ENV DISPLAY=:99

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default command (Xvfb entrypoint starts virtual display before CMD)
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]