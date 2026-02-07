#!/bin/bash
# =============================================================
# Deploy script for Yandex Maps Bot
# This script runs on the SERVER to pull latest code and restart
# =============================================================

set -e

# Configuration — edit these
APP_DIR="/home/$USER/yandex-maps-bot"
BRANCH="main"
COMPOSE_PROFILES="postgres redis app celery_worker celery_beat"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}🚀 Deploying Yandex Maps Bot...${NC}"
echo "=============================="

# Navigate to project directory
cd "$APP_DIR" || { echo -e "${RED}❌ Directory $APP_DIR not found!${NC}"; exit 1; }

# Pull latest code from GitHub
echo -e "${YELLOW}📥 Pulling latest code from GitHub...${NC}"
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
echo -e "${GREEN}✅ Code updated${NC}"

# Check if .env exists
if [ ! -f .env ]; then
    echo -e "${RED}❌ .env file not found! Copy from .env.example:${NC}"
    echo "   cp .env.example .env && nano .env"
    exit 1
fi

# Build and restart containers
echo -e "${YELLOW}🔨 Building Docker images...${NC}"
docker compose build --no-cache

echo -e "${YELLOW}🔄 Restarting services...${NC}"
docker compose down
docker compose up -d $COMPOSE_PROFILES

# Wait for health checks
echo -e "${YELLOW}⏳ Waiting for services to start...${NC}"
sleep 10

# Check health
echo -e "${YELLOW}🏥 Checking health...${NC}"
if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Application is healthy!${NC}"
else
    echo -e "${RED}⚠️  Health check failed. Checking logs...${NC}"
    docker compose logs --tail=20 app
fi

# Show running containers
echo ""
echo -e "${GREEN}📦 Running containers:${NC}"
docker compose ps

# Cleanup old images
echo -e "${YELLOW}🧹 Cleaning up old Docker images...${NC}"
docker image prune -f

echo ""
echo -e "${GREEN}✅ Deploy complete!${NC}"
echo "=============================="
echo "🌐 App:    http://$(hostname -I | awk '{print $1}'):8000"
echo "🌸 Flower: http://$(hostname -I | awk '{print $1}'):5555"
echo "📊 Logs:   docker compose logs -f"
