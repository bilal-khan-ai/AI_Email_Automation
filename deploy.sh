#!/bin/bash
# Deployment script for AI Email Automation
# Usage: ./deploy.sh [VM_IP] [SSH_USER] [build|update] [PASSWORD]
# Example: ./deploy.sh vm-linux-user1@40.81.231.178 build - for rebuilding
# Example: ./deploy.sh vm-linux-user1@40.81.231.178 - for updating           
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

PROJECT_DIR="AI_Email_Automation"

# --- Password Handling (Industry Standard) ---
# 1. Use argument if provided, otherwise prompt securely
if [ -n "$ARG_PASS" ]; then
    PASSWORD="$ARG_PASS"
else
    echo -n "🔑 Enter VM Password for $SSH_USER: "
    read -s PASSWORD
    echo ""
fi

# 2. Export for sshpass (prevents password appearing in 'ps' output)
export SSHPASS="$PASSWORD"

# 3. Configure SSH/SCP commands
SSH_CMD="ssh"
SCP_CMD="scp"
if [ -n "$PASSWORD" ]; then
    if command -v sshpass &> /dev/null; then
        # Use -e to read from SSHPASS environment variable
        SSH_CMD="sshpass -e ssh -o StrictHostKeyChecking=no"
        SCP_CMD="sshpass -e scp -o StrictHostKeyChecking=no"
    else
        echo "⚠️  sshpass not found. Falling back to manual password prompts."
    fi
fi

echo "🚀 Deploying to $SSH_USER@$VM_IP ($MODE mode)..."

# Step 1: Package files locally
echo "📦 Packing files..."
tar -czvf deploy_pkg.tar.gz --exclude='deploy_pkg.tar.gz' --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='uploads' --exclude='Books' --exclude='data' --exclude='chroma_db' --exclude='chromaDB' .

# Step 2: Upload
echo "📤 Uploading package..."
$SSH_CMD $SSH_USER@$VM_IP "mkdir -p ~/$PROJECT_DIR"
$SCP_CMD deploy_pkg.tar.gz $SSH_USER@$VM_IP:~/$PROJECT_DIR/
rm deploy_pkg.tar.gz

# Step 3: Remote Execution
echo "⚙️  Executing remote deployment..."
$SSH_CMD $SSH_USER@$VM_IP << EOF
    set -e
    cd ~/$PROJECT_DIR

    # Sudo helper that uses the passed password
    SUDO="echo '$PASSWORD' | sudo -S"

    echo "📂 Extracting files..."
    tar -xzvf deploy_pkg.tar.gz
    rm deploy_pkg.tar.gz

    # SWAP setup
    if [ ! -f /swapfile ]; then
        eval "\$SUDO fallocate -l 4G /swapfile" || eval "\$SUDO dd if=/dev/zero of=/swapfile bs=1M count=4096"
        eval "\$SUDO chmod 600 /swapfile"
        eval "\$SUDO mkswap /swapfile"
        eval "\$SUDO swapon /swapfile"
    fi

    # Docker check & Install
    if ! command -v docker &> /dev/null; then
        curl -fsSL https://get.docker.com -o get-docker.sh
        eval "\$SUDO sh get-docker.sh"
        eval "\$SUDO usermod -aG docker \$USER"
    fi

    # Docker Compose check & Install
    if ! command -v docker-compose &> /dev/null; then
        eval "\$SUDO curl -L \"https://github.com/docker/compose/releases/latest/download/docker-compose-\$(uname -s)-\$(uname -m)\" -o /usr/local/bin/docker-compose"
        eval "\$SUDO chmod +x /usr/local/bin/docker-compose"
    fi

    # Deployment
    if [ "$MODE" == "build" ]; then
        eval "\$SUDO docker-compose up -d --build"
    else
        eval "\$SUDO docker-compose up -d"
        eval "\$SUDO docker-compose restart web"
    fi
    eval "\$SUDO docker-compose restart worker"

    echo "✅ Deployment successful!"
    eval "\$SUDO docker-compose ps"
EOF

echo "🏁 Done."
