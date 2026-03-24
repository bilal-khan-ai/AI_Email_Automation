# Deployment Guide for AI Email Automation

This guide covers deploying the AI Email Automation project from your local machine to the remote Azure Linux VM using Docker.

## Prerequisites
- **Git Bash** or **WSL** (Windows Subsystem for Linux) on your local machine if you are running Windows, so you can run the `deploy.sh` script.
- **SSH Access** configured to the remote VM.
- Make sure you have your `.env` file properly configured in the project root directory locally. It will be securely transferred to the VM.

## Step 1: Run the Deployment Script

We have created an automated deployment script (`deploy.sh`) that will:
1. Copy your project files to the VM (ignoring local temporary files like `.venv`, `__pycache__`, etc.).
2. Connect to the VM via SSH.
3. Automatically install Docker and Docker Compose if they are missing.
4. Build the Docker images and start the services (`db`, `web`, `worker`).

Open your terminal (Git Bash, WSL, or PowerShell if it supports ssh/rsync) and run:

```bash
cd /path/to/AI_Email_Automation
bash deploy.sh 40.81.231.178 vm-linux-user1
```

> **Note**: If you haven't set up SSH keys, it will prompt you for the `vm-linux-user1` password multiple times (once for rsync, once for ssh).

## Step 2: Verifying the Deployment

If the script completes successfully without errors, you can manually SSH into your VM to verify:

```bash
ssh vm-linux-user1@40.81.231.178
cd ~/AI_Email_Automation
```

Check the status of the containers:
```bash
sudo docker-compose ps
```

You should see 3 containers running:
- `ai_email_automation-db-1` (Postgres 16)
- `ai_email_automation-web-1` (Dashboard)
- `ai_email_automation-worker-1` (Main Daemon)

To view live logs from the backend worker (to see if emails are being fetched):
```bash
sudo docker-compose logs -f worker
```

To view live logs from the web dashboard:
```bash
sudo docker-compose logs -f web
```

## Step 3: Accessing the Dashboard

The web dashboard is exposed on port `5000`.

To access it, open your browser and navigate to:
`http://40.81.231.178:5000/`

> **Firewall Note**: Ensure that port `5000` is open on the Azure Network Security Group (NSG) associated with the VM `40.81.231.178`.

Log in using the credentials defined or stored in your PostgreSQL database. By default, the system looks at the credentials established in your `.env` or custom auth system.

## Troubleshooting

- **Database Connection Issues**: Ensure your `.env` contains `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`, so Docker Compose automatically initializes the DB with these parameters.
- **Permission Denied for Docker**: The `deploy.sh` script attempts to add your user to the `docker` group, but sometimes it requires a fresh login. If you get `permission denied while trying to connect to the Docker daemon`, use `sudo docker-compose` instead.
- **Missing Data or Emails**: Since `data/`, `chroma_db/`, `chromaDB/`, and `uploads/` were ignored during upload to save bandwidth, the project will start fresh on the remote VM. If you needed to upload your existing vectorized database, comment out those lines inside `.dockerignore` before running `deploy.sh`.
