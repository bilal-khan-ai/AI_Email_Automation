#!/bin/bash

# Target VM credentials
VM_IP="40.81.231.178"
SSH_USER="vm-linux-user1"

# Database credentials for the new user
NEW_DB="a_db"
NEW_USER="anil_s"
NEW_PASS="pass123"

echo "Connecting to VM $VM_IP to create database '$NEW_DB' and user '$NEW_USER'..."

ssh ${SSH_USER}@${VM_IP} << EOF
    cd ~/AI_Email_Automation
    CONTAINER_ID=\$(docker compose ps -q db)
    
    if [ -z "\$CONTAINER_ID" ]; then
        echo "Error: Could not find the database container. Is docker-compose running?"
        exit 1
    fi
    
    echo "Creating database..."
    docker exec \$CONTAINER_ID psql -U postgres -c "CREATE DATABASE ${NEW_DB};"
    
    echo "Creating user..."
    docker exec \$CONTAINER_ID psql -U postgres -c "CREATE USER ${NEW_USER} WITH ENCRYPTED PASSWORD '${NEW_PASS}';"
    
    echo "Granting privileges..."
    docker exec \$CONTAINER_ID psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE ${NEW_DB} TO ${NEW_USER};"
    
    echo "Granting public schema privileges..."
    docker exec \$CONTAINER_ID psql -U postgres -d ${NEW_DB} -c "GRANT ALL ON SCHEMA public TO ${NEW_USER};"
    
    echo "Database and user created successfully!"
EOF

echo "Done!"
