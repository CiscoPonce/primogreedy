#!/bin/bash
# Deploy PrimoGreedy Data API to VPS
# Usage: bash vps/deploy.sh

set -e

VPS="ubuntu@100.110.105.118"
REMOTE_DIR="/home/ubuntu/primogreedy"

echo "=== PrimoGreedy VPS Deploy ==="

echo "1. Creating remote directory..."
ssh $VPS "mkdir -p $REMOTE_DIR"

echo "2. Copying files..."
scp vps/api.py vps/requirements.txt vps/.env vps/schema.sql $VPS:$REMOTE_DIR/

echo "3. Installing Python dependencies..."
ssh $VPS "cd $REMOTE_DIR && pip3 install --break-system-packages -r requirements.txt"

echo "4. Creating systemd service..."
ssh $VPS "sudo tee /etc/systemd/system/primogreedy-api.service > /dev/null << 'EOF'
[Unit]
Description=PrimoGreedy Data API
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/primogreedy
ExecStart=/usr/bin/python3 -m uvicorn api:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5
EnvironmentFile=/home/ubuntu/primogreedy/.env

[Install]
WantedBy=multi-user.target
EOF"

echo "5. Starting service..."
ssh $VPS "sudo systemctl daemon-reload && sudo systemctl enable primogreedy-api && sudo systemctl restart primogreedy-api"

echo "6. Checking status..."
sleep 2
ssh $VPS "sudo systemctl status primogreedy-api --no-pager -l" || true

echo ""
echo "=== Deploy complete ==="
echo "Health check: curl http://100.110.105.118:8000/health"
