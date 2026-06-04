# 1. 使用官方 Python 瘦身版映像檔 (兼顧體積與完美的套件相容性)
FROM python:3.11-slim

# 2. 設定環境變數
# 避免 Python 寫入 .pyc 隱藏檔，減少容器體積
ENV PYTHONDONTWRITEBYTECODE=1
# 讓 print() 與日誌輸出直接送到終端機，這對於除錯 LLM API 的非同步回應非常重要
ENV PYTHONUNBUFFERED=1

# 3. 建立工作目錄
WORKDIR /app

# 4. 安裝系統級依賴 (若未來需要編譯某些套件可在此加入，目前保持最簡)
RUN apt-get update && apt-get install -y \
    --no-install-recommends \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 5. 依賴安裝分離 (利用 Docker 快取機制加速部署)
# 先只複製 requirements.txt，這樣只要依賴沒變，就不會重新執行 pip install
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 6. 複製專案原始碼
COPY . .

# 7. 安全性升級：建立並切換為非 root 使用者
# 在處理包含 API Keys 或自動化生成內容的服務時，降權執行是防止容器逃逸的標準防護
RUN useradd -m apiuser && chown -R apiuser /app
USER apiuser

# 8. 宣告對外 Port
EXPOSE 8000

# 9. 啟動指令 (FastAPI 實例位於 app/main.py)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]