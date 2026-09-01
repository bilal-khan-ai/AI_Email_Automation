#!/bin/bash
# Bilal Khan (12/08/2026) Issue No  Sheet_Name  - Safe deployment script targeting web and worker services only (leaves db container untouched)
# Safe deployment script for AI Email Automation
# Usage: ./safe_deploy.sh [VM_IP] [SSH_USER] [build|update] [PASSWORD]
# Example: ./safe_deploy.sh vm-linux-user1@40.81.231.178 build - for rebuilding web/worker only
# Example: ./safe_deploy.sh vm-linux-user1@40.81.231.178 - for updating web/worker only

# Handle user@ip or separate arguments
if [[ "$1" == *"@"* ]]; then
    SSH_USER=$(echo "$1" | cut -d'@' -f1)
    VM_IP=$(echo "$1" | cut -d'@' -f2)
    MODE=${2:-"update"}
    ARG_PASS=$3
else
    VM_IP=${1:-"40.81.231.178"}
    SSH_USER=${2:-"vm-linux-user1"}
    MODE=${3:-"update"}
    ARG_PASS=$4
fi

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

PROJECT_DIR="AI_Email_Automation"

# Check for .env file
if [ ! -f .env ]; then
    echo "❌ Error: .env file missing in project root ($PROJECT_ROOT)!"
    exit 1
fi
# Bilal Khan (01/09/2026) Issue No  Sheet_Name  - Dynamic Project Root Resolution for Deployment Scripts - end

# --- Password Handling ---
if [ -n "$ARG_PASS" ]; then
    PASSWORD="$ARG_PASS"
else
    echo -n "🔑 Enter VM Password for $SSH_USER: "
    read -s PASSWORD
    echo ""
fi

export SSHPASS="$PASSWORD"

# Configure SSH/SCP commands
SSH_CMD="ssh"
SCP_CMD="scp"
if [ -n "$PASSWORD" ]; then
    if command -v sshpass &> /dev/null; then
        SSH_CMD="sshpass -e ssh -o StrictHostKeyChecking=no"
        SCP_CMD="sshpass -e scp -o StrictHostKeyChecking=no"
    else
        echo "⚠️  sshpass not found. Falling back to manual password prompts."
    fi
fi

echo "🚀 Safe Deploying (web & worker ONLY, db UNTOUCHED) to $SSH_USER@$VM_IP ($MODE mode)..."

# Step 1: Package files locally
echo "📦 Packing files..."
tar -czvf deploy_pkg.tar.gz \
    --exclude='deploy_pkg.tar.gz' \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='uploads' \
    --exclude='Books' \
    --exclude='data' \
    --exclude='chroma_db' \
    --exclude='chromaDB' \
    --exclude='scratch' \
    --exclude='scripts/database/backups' \
    --exclude='*.dump' \
    --exclude='*.sql' \
    --exclude='*.db' .

# Step 2: Upload
echo "📤 Uploading package..."
$SSH_CMD $SSH_USER@$VM_IP "mkdir -p ~/$PROJECT_DIR"
$SCP_CMD deploy_pkg.tar.gz $SSH_USER@$VM_IP:~/$PROJECT_DIR/
rm deploy_pkg.tar.gz

# Step 3: Remote Execution (Targeting ONLY web and worker)
echo "⚙️  Executing safe remote deployment (protecting db container)..."
$SSH_CMD $SSH_USER@$VM_IP << EOF
    set -e
    cd ~/$PROJECT_DIR

    SUDO="echo '$PASSWORD' | sudo -S"

    echo "📂 Extracting files..."
    tar -xzvf deploy_pkg.tar.gz
    rm deploy_pkg.tar.gz

# Bilal Khan (18/08/2026) Issue No  Sheet_Name  - Bulletproof swap verification, auto docker cache cleanup, and system health checks - start
    # Step 3.1: Bulletproof SWAP Setup (Check active swap, create 4G if missing, persist in fstab)
    if ! swapon --show | grep -q '/swapfile'; then
        echo "🔧 Configuring 4GB Swap Space..."
        if [ ! -f /swapfile ] || [ $(stat -c%s /swapfile 2>/dev/null || echo 0) -lt 4000000000 ]; then
            eval "\$SUDO swapoff /swapfile 2>/dev/null || true"
            eval "\$SUDO rm -f /swapfile"
            eval "\$SUDO fallocate -l 4G /swapfile" || eval "\$SUDO dd if=/dev/zero of=/swapfile bs=1M count=4096"
            eval "\$SUDO chmod 600 /swapfile"
            eval "\$SUDO mkswap /swapfile"
        fi
        eval "\$SUDO swapon /swapfile"
    fi
    # Ensure swap persists across reboots in /etc/fstab
    if ! grep -q '/swapfile' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' | eval "\$SUDO tee -a /etc/fstab"
    fi

    # Step 3.2: Docker check & Install
    if ! command -v docker &> /dev/null; then
        curl -fsSL https://get.docker.com -o get-docker.sh
        eval "\$SUDO sh get-docker.sh"
        eval "\$SUDO usermod -aG docker \$USER"
    fi

    # Step 3.3: Docker Compose check & Aliasing (v2 or v1)
    if docker compose version &> /dev/null; then
        COMPOSE_CMD="docker compose"
    elif command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="docker-compose"
    else
        eval "\$SUDO curl -L \"https://github.com/docker/compose/releases/latest/download/docker-compose-\$(uname -s)-\$(uname -m)\" -o /usr/local/bin/docker-compose"
        eval "\$SUDO chmod +x /usr/local/bin/docker-compose"
        COMPOSE_CMD="docker-compose"
    fi

    # Step 3.4: Safe Deployment (ONLY web and worker, db UNTOUCHED)
    if [ "$MODE" == "build" ]; then
        eval "\$SUDO \$COMPOSE_CMD up -d --no-deps --build web worker"
    else
        eval "\$SUDO \$COMPOSE_CMD up -d --no-deps web worker"
        eval "\$SUDO \$COMPOSE_CMD restart web worker"
    fi

    # Step 3.5: Auto-Prune dangling build cache to prevent disk exhaustion
    echo "🧹 Cleaning up temporary build cache and dangling images..."
    eval "\$SUDO docker builder prune -f 2>/dev/null || true"
    eval "\$SUDO docker image prune -f 2>/dev/null || true"

    echo "✅ Safe deployment successful! (db container was not touched)"
    eval "\$SUDO \$COMPOSE_CMD ps web worker"

    # Step 3.6: Report System Health Status
    echo "📊 --- SYSTEM HEALTH REPORT ---"
    free -h
    df -h /
    # Bilal Khan (18/08/2026) Issue No  Sheet_Name  - Bulletproof swap verification, auto docker cache cleanup, and system health checks - end
EOF

echo "🏁 Done."
