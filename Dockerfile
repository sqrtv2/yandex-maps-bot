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
    dbus \
    libnss3 \
    libnspr4 \
    libatk1.0-0t64 \
    libatk-bridge2.0-0t64 \
    libxkbcommon0 \
    libatspi2.0-0t64 \
    libasound2t64 \
    libcups2t64 \
    libdrm2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libdbus-1-3 \
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

# Replace original playwright module with rebrowser-playwright (anti-CDP-detection patches)
# rebrowser-playwright provides module rebrowser_playwright, symlink makes it available as playwright too
RUN pip uninstall -y playwright && rm -rf /usr/local/lib/python3.11/site-packages/playwright && \
    ln -s /usr/local/lib/python3.11/site-packages/rebrowser_playwright /usr/local/lib/python3.11/site-packages/playwright

# Install Chromium browser via rebrowser-playwright (patched driver)
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN python -m rebrowser_playwright install chromium

# Install patchright's bundled patched Chromium (for A/B-testing backend).
# patchright applies patches both at the driver layer AND in the bundled chromium
# binary (e.g. console.debug source URL, runtime evaluation tricks). Using
# patchright + non-patchright chromium would defeat half the protections, so we
# install patchright's own browser into the same browsers path.
RUN python -m patchright install chromium

# Camoufox — anti-detect Firefox fork. Used by tasks.warmup_camoufox in a
# dedicated celery worker (subprocess-isolated; sync API can't share asyncio
# loop with chromium sync_playwright). `camoufox fetch` downloads the patched
# binary into ~/.cache/camoufox; install it under /opt so it's accessible to
# the appuser at runtime via a symlink.
# Firefox-specific libs that chromium doesn't pull (libdbus-glib, libxt) — we
# install manually because `playwright install-deps firefox` requests packages
# (ttf-ubuntu-font-family, etc.) that don't exist on debian bookworm.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libdbus-glib-1-2 \
    libxt6 \
    libxtst6 \
    libgtk-3-0 \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /opt/camoufox-cache && \
    HOME=/opt/camoufox-cache python -m camoufox fetch

# Download Chrome 145 for better Yandex SmartCaptcha fingerprint compatibility
# rebrowser-playwright 1.52 ships Chromium 136, but Chrome 145 passes more checks.
# browser_manager.py globs /opt/pw-browsers/chromium-*/chrome-linux*/chrome and uses the newest.
RUN wget -q https://cdn.playwright.dev/chrome-for-testing-public/145.0.7632.6/linux64/chrome-linux64.zip -O /tmp/chrome145.zip && \
    mkdir -p /opt/pw-browsers/chromium-1208 && \
    cd /opt/pw-browsers/chromium-1208 && \
    python -c "import zipfile; zipfile.ZipFile('/tmp/chrome145.zip').extractall('.')" && \
    rm /tmp/chrome145.zip && \
    chmod -R +x /opt/pw-browsers/chromium-1208/chrome-linux64/

# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p /app/logs /app/screenshots /app/data /app/browser_profiles /app/downloads

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app && \
    mkdir -p /home/appuser/.cache && \
    cp -r /opt/camoufox-cache/.cache/camoufox /home/appuser/.cache/camoufox && \
    chown -R appuser:appuser /home/appuser/.cache

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