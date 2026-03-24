#!/bin/bash
# Deployment script for AI Email Automation
# Usage: ./deploy.sh [VM_IP_ADDRESS] [SSH_USER]
# Alternatively: ./deploy.sh vm-linux-user1@40.81.231.178

VM_IP=${1:-"40.81.231.178"}
SSH_USER=${2:-"vm-linux-user1"}
PROJECT_DIR="AI_Email_Automation"

# If the user passes user@ip as the first argument, handle it correctly
if [[ "$VM_IP" == *"@"* ]]; then
    SSH_USER=$(echo $VM_IP | cut -d'@' -f1)
    VM_IP=$(echo $VM_IP | cut -d'@' -f2)
fi

echo "Deploying to $SSH_USER@$VM_IP..."

# Step 1: Package files locally (avoids missing 'rsync' on Windows Git Bash)
echo "Packing files locally..."
tar -czvf deploy_pkg.tar.gz --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='uploads' --exclude='Books' --exclude='data' --exclude='chroma_db' --exclude='chromaDB' .

# Step 2: Ensure directory exists & SCP upload
echo "Uploading package to remote server..."
ssh $SSH_USER@$VM_IP "mkdir -p ~/$PROJECT_DIR"
scp deploy_pkg.tar.gz $SSH_USER@$VM_IP:~/$PROJECT_DIR/

# Cleanup local tar file
rm deploy_pkg.tar.gz

# Step 3: Extract and start docker on VM
echo "Executing deployment on remote server..."
ssh $SSH_USER@$VM_IP << EOF
    set -e
    cd ~/$PROJECT_DIR

    echo "Extracting package..."
    tar -xzvf deploy_pkg.tar.gz
    rm deploy_pkg.tar.gz

    # Install Docker if not present
    if ! command -v docker &> /dev/null; then
        echo "Docker not found. Installing Docker..."
        curl -fsSL https://get.docker.com -o get-docker.sh
        sudo sh get-docker.sh
        sudo usermod -aG docker \$USER
        echo "Docker installed. You may need to log out and log back in for group changes to take effect."
    fi

    # Install Docker Compose if not present
    if ! command -v docker-compose &> /dev/null; then
        echo "Docker Compose not found. Installing..."
        sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-\$(uname -s)-\$(uname -m)" -o /usr/local/bin/docker-compose
        sudo chmod +x /usr/local/bin/docker-compose
    fi

    # Build and start the containers
    echo "Building and starting Docker containers..."
    sudo docker-compose up -d --build

    echo "✅ Deployment successful! The containers are running in the background."
    sudo docker-compose ps
EOF

echo "Deployment complete."
