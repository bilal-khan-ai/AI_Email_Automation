#!/bin/bash
# Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Local safe deployment script targeting web and worker services only (db untouched if running, auto-started if stopped) - start
# Safe deployment script for AI Email Automation (Local Docker)
# Usage: ./safe_deploy_local.sh [build|update]
# Example: ./safe_deploy_local.sh build  - rebuilds web/worker containers locally
# Example: ./safe_deploy_local.sh update - updates/restarts web/worker containers locally

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

echo "🚀 Local Safe Deployment starting (web & worker ONLY) ($MODE mode)..."

# Ensure DB container is running before launching web and worker
if ! $COMPOSE_CMD ps --services --filter "status=running" | grep -q "^db$"; then
    echo "⚠️  Database container (db) is not running. Starting db container first..."
    $COMPOSE_CMD up -d db
    sleep 3
fi

# Step 1: Safe deployment targeting web and worker
if [ "$MODE" == "build" ]; then
    $COMPOSE_CMD up -d --no-deps --build web worker
else
    $COMPOSE_CMD up -d --no-deps web worker
    $COMPOSE_CMD restart web worker
fi

# Step 2: Clean up build cache
echo "🧹 Cleaning up local temporary build cache..."
docker builder prune -f 2>/dev/null || true
docker image prune -f 2>/dev/null || true

echo "✅ Local safe deployment successful!"
$COMPOSE_CMD ps

# Step 3: Report Container Memory & CPU Resource Usage
echo "📊 --- LOCAL CONTAINER RESOURCE USAGE ---"
docker stats --no-stream
# Bilal Khan (28/08/2026) Issue No  Sheet_Name  - Local safe deployment script targeting web and worker services only (db untouched if running, auto-started if stopped) - end
