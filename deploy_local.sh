#!/bin/bash

# =========================================================
# 用途：在本機 (CasaOS) 建構並部署 FluencyTides 前後端容器。
# Purpose: Build and deploy the FluencyTides backend/frontend
#          containers locally (CasaOS environment).
# =========================================================
# 使用範例：/ Usage:
# 1. 賦予執行權限：
#    chmod +x deploy_local.sh
# 2. 執行部屬腳本：
#    ./deploy_local.sh
# =========================================================

# 發生錯誤時立即終止 / Exit immediately on any error
set -e

echo "========================================================="
echo " FluencyTides Local Deployment Script (CasaOS Transition) "
echo "========================================================="

# 切換到腳本所在目錄，確保相對路徑 ./backend 與 ./frontend 正確 / Change to the script's directory so relative paths resolve correctly
cd "$(dirname "$0")"

# 1. 確保 Docker 網路存在 (兩者 docker-compose.yml 中都有 external: true) / Ensure the shared Docker network exists (both compose files declare it as external)
NETWORK_NAME="fluencytides_net"
if ! docker network ls | grep -qw "$NETWORK_NAME"; then
    echo "Creating Docker network: $NETWORK_NAME..."
    docker network create "$NETWORK_NAME"
else
    echo "Docker network '$NETWORK_NAME' already exists."
fi

# 2. 建構後端映像檔 (與 main.yml 命名一致) / Build the backend image (naming consistent with main.yml)
echo "---------------------------------------------------------"
echo "Building backend Docker image..."
echo "---------------------------------------------------------"
docker build -t ghcr.io/jacky917/fluencytides-backend:latest ./backend

# 3. 建構前端映像檔 (與 main.yml 命名一致) / Build the frontend image (naming consistent with main.yml)
echo "---------------------------------------------------------"
echo "Building frontend Docker image..."
echo "---------------------------------------------------------"
docker build -t ghcr.io/jacky917/fluencytides-frontend:latest ./frontend

# 4. 部屬後端容器 / Deploy the backend container
echo "---------------------------------------------------------"
echo "Deploying backend container..."
echo "---------------------------------------------------------"
# 注意：若 CasaOS 環境支援 docker compose (v2)，請使用 docker compose
# 若為舊版，則保留 docker-compose
if docker compose version > /dev/null 2>&1; then
    docker compose -f backend/docker-compose.yml up -d --force-recreate
else
    docker-compose -f backend/docker-compose.yml up -d --force-recreate
fi

# 5. 部屬前端容器 / Deploy the frontend container
echo "---------------------------------------------------------"
echo "Deploying frontend container..."
echo "---------------------------------------------------------"
if docker compose version > /dev/null 2>&1; then
    docker compose -f frontend/docker-compose.yml up -d --force-recreate
else
    docker-compose -f frontend/docker-compose.yml up -d --force-recreate
fi

# 取得本機 IP 位址 / Get the local host IP address
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -z "$HOST_IP" ]; then
    HOST_IP="localhost"
fi

# 動態取得映射的 Port / Dynamically resolve the mapped host ports
BACKEND_PORT=$(docker port fluencytides-backend 8000/tcp 2>/dev/null | awk -F ':' '{print $2}' | head -n 1)
if [ -z "$BACKEND_PORT" ]; then BACKEND_PORT="8000"; fi

FRONTEND_PORT=$(docker port fluencytides-frontend 80/tcp 2>/dev/null | awk -F ':' '{print $2}' | head -n 1)
if [ -z "$FRONTEND_PORT" ]; then FRONTEND_PORT="8080"; fi

# 6. 清除無用的 Docker 映像檔（釋放磁碟空間） / Prune unused Docker images (free up disk space)
# ⚠️ 警告：docker image prune 會刪除所有「未被任何容器使用」的 dangling 映像檔。
#    這包含舊版的建構快取、中間層映像等。如果你有其他專案依賴這些映像，請選擇 N。
#    此操作不會影響正在運行的容器，只會清除已被新版取代的舊映像。
echo "---------------------------------------------------------"
echo "⚠️  清除無用的 Docker 映像檔 (dangling images)"
echo "   這會刪除所有未被容器使用的舊版映像，釋放磁碟空間。"
echo "   正在運行的容器不會受到影響。"
echo "---------------------------------------------------------"
read -p "是否清除無用映像檔？(y/N): " PRUNE_CONFIRM
if [ "$PRUNE_CONFIRM" = "y" ] || [ "$PRUNE_CONFIRM" = "Y" ]; then
    docker image prune -f
    echo "✅ 無用映像檔已清除。"
else
    echo "⏭️  跳過清除。"
fi

echo "========================================================="
echo " Deployment Complete!"
echo " - Backend API: http://${HOST_IP}:${BACKEND_PORT}/docs"
echo " - Frontend UI: http://${HOST_IP}:${FRONTEND_PORT}"
echo " (Make sure /DATA/AppData/FluencyTides/backend/.env is set)"
echo "========================================================="
