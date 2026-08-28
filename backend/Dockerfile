# 1. 使用官方 Python 瘦身版映像檔 (兼顧體積與完美的套件相容性)
FROM python:3.13-slim

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

# 7.5 安裝 Claude Code CLI（LLM_PROVIDER=claude-code 模式的執行檔）
# - 以 apiuser 身分安裝到 ~/.local/bin（native installer 免 Node，自帶依賴），
#   與非 root 設計相容；後端的 CLI 路徑探測會經 PATH 直接命中。
# - 版本可用 build arg 釘住（建議部署時明確指定，例：--build-arg CLAUDE_CODE_VERSION=2.x.y），
#   預設 latest。
# - 認證：容器內無法互動登入，需在 .env 設 LLM_CLAUDE_CODE_OAUTH_TOKEN
#   （由已登入機器執行 `claude setup-token` 產生），詳見
#   docs/wip/claude_cli_in_container_FEAT_2026-08-29.md §5。
# - 對非 claude-code 模式零影響：CLI 只是靜態檔案，google/openai 路徑不會載入它。
ARG CLAUDE_CODE_VERSION=latest
RUN curl -fsSL https://claude.ai/install.sh | bash -s -- ${CLAUDE_CODE_VERSION}
ENV PATH="/home/apiuser/.local/bin:${PATH}"

# 8. 宣告對外 Port
EXPOSE 8000

# 9. 啟動指令 (FastAPI 實例位於 app/main.py)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]