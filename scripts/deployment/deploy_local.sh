#!/bin/bash
# Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Local deployment script for testing Docker containers locally - start
# Deployment script for AI Email Automation (Local Docker)
# Usage: ./deploy_local.sh [build|update]
# Example: ./deploy_local.sh build  - rebuilds all containers locally
# Example: ./deploy_local.sh update - updates/restarts containers locally

MODE=${1:-"update"}

# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Dynamic Project Root Resolution for Deployment Scripts - start
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/../../docker-compose.yml" ]; then
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
elif [ -f "$SCRIPT_DIR/docker-compose.yml" ]; then
    PROJECT_ROOT="$SCRIPT_DIR"
else
    PROJECT_ROOT="$(pwd)"
fi
cd "$PROJECT_ROOT"

# Check for .env file
if [ ! -f .env ]; then
    echo "❌ Error: .env file missing in project root ($PROJECT_ROOT)!"
    exit 1
fi
# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Dynamic Project Root Resolution for Deployment Scripts - end

# Detect Docker Compose command
if docker compose version &> /dev/null; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose &> /dev/null; then
    COMPOSE_CMD="docker-compose"
else
    echo "❌ Error: Docker Compose is not installed or not in PATH."
    exit 1
fi

echo "🚀 Local Deployment starting ($MODE mode)..."

# Step 1: Up and Build
if [ "$MODE" == "build" ]; then
    $COMPOSE_CMD up -d --build
else
    $COMPOSE_CMD up -d
    $COMPOSE_CMD restart web
fi
$COMPOSE_CMD restart worker

# Step 2: Clean up build cache
echo "🧹 Cleaning up local temporary build cache..."
docker builder prune -f 2>/dev/null || true
docker image prune -f 2>/dev/null || true

echo "✅ Local deployment successful!"
$COMPOSE_CMD ps

# Step 3: Report Container Memory & CPU Resource Usage
echo "📊 --- LOCAL CONTAINER RESOURCE USAGE ---"
docker stats --no-stream
# Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Local deployment script for testing Docker containers locally - end
