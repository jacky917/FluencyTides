# Stage 1: 建置階段 (Build Stage)
FROM node:20-alpine AS builder

# 1. 建立並切換工作目錄
WORKDIR /app

# 2. 複製 package.json 和 package-lock.json
# 利用 Docker 快取，只有在依賴變更時才重新安裝 npm 套件
COPY package*.json ./

# 3. 安裝依賴 (使用 ci 確保依照 lock 檔精確安裝)
RUN npm ci

# 4. 複製前端所有原始碼
COPY . .

# 5. 執行 Vite 建置
RUN npm run build

# ==========================================

# Stage 2: 運行階段 (Production Stage)
FROM nginx:alpine

# 1. 移除 Nginx 預設的設定檔
RUN rm /etc/nginx/conf.d/default.conf

# 2. 複製我們自訂的 nginx.conf (替換掉 default.conf)
# 這樣 Nginx 才會套用您的 API Proxy 和 SPA 路由設定
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 3. 建立 Nginx 設定檔中指定的根目錄
RUN mkdir -p /usr/share/nginx/FluencyTides

# 4. 從 builder 階段把編譯好的 dist 複製到 Nginx 的根目錄
COPY --from=builder /app/dist /usr/share/nginx/FluencyTides

# 5. 宣告對外 Port (與 nginx.conf 一致)
EXPOSE 80

# 6. 啟動 Nginx，並保持在前景執行
CMD ["nginx", "-g", "daemon off;"]
