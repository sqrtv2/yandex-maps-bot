#!/bin/bash
# Entrypoint: start Xvfb virtual display, then run the actual command.
# This allows Chrome to run in headed mode (no --headless), avoiding
# all headless detection vectors while running inside Docker.

# Start Xvfb on display :99 with a reasonable resolution
# -ac disables access control (no auth needed)
# -screen 0 sets screen 0 resolution
Xvfb :99 -ac -screen 0 1920x1080x24 -nolisten tcp &
XVFB_PID=$!

# Wait for Xvfb to be ready
sleep 1

# Export DISPLAY so Chrome uses the virtual framebuffer
export DISPLAY=:99

echo "🖥️ Xvfb started on :99 (PID=$XVFB_PID)"

# Execute the original command (CMD from docker-compose)
exec "$@"
