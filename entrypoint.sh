#!/bin/bash
# Entrypoint: start Xvfb virtual display, then run the actual command.
# This allows Chrome to run in headed mode (no --headless), avoiding
# all headless detection vectors while running inside Docker.

# Kill any stale Xvfb from previous runs
pkill -f 'Xvfb :99' 2>/dev/null || true
rm -f /tmp/.X99-lock 2>/dev/null || true

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

# Background watchdog: restart Xvfb if it dies
(
  while true; do
    sleep 10
    if ! kill -0 "$XVFB_PID" 2>/dev/null; then
      echo "⚠️ Xvfb died, restarting..."
      rm -f /tmp/.X99-lock 2>/dev/null || true
      Xvfb :99 -ac -screen 0 1920x1080x24 -nolisten tcp &
      XVFB_PID=$!
      echo "🖥️ Xvfb restarted (PID=$XVFB_PID)"
    fi
  done
) &

# Execute the original command (CMD from docker-compose)
exec "$@"
