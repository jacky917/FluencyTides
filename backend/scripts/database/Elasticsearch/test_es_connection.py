"""Elasticsearch 連線測試腳本：檢查叢集資訊與 Sudachi 插件。

Elasticsearch connection test script: checks cluster info and verifies
the Sudachi analysis plugin is installed.
"""

import sys
import os
import asyncio
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.core.config import settings
import aiohttp

# 解決 Windows 命令提示字元輸出 Emoji 時的 cp950 編碼錯誤
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

async def test_elasticsearch_connection():
    """測試 ES 連線並列出插件，確認 Sudachi 是否可用。

    Test the ES connection and list plugins to confirm whether the
    Sudachi plugin is available.
    """
    # 優先使用 PUBLIC URL 進行測試 (因為這是從本機 Windows 測試)
    es_url = os.getenv("ELASTICSEARCH_PUBLIC_URL") or os.getenv("ELASTICSEARCH_HOSTS", "")
    es_username = os.getenv("ELASTICSEARCH_USERNAME", "")
    es_password = os.getenv("ELASTICSEARCH_PASSWORD", "")

    if not es_url:
        print("❌ 錯誤：找不到 ELASTICSEARCH_PUBLIC_URL 或 ELASTICSEARCH_HOSTS！")
        print("請檢查 .env 設定。")
        return

    print("🔌 正在嘗試連線至 Elasticsearch...")
    print("--------------------------------------------------")
    print(f"📌 連線目標: {es_url}")
    print(f"📌 使用者帳號: {es_username}")
    print("--------------------------------------------------\n")

    auth = aiohttp.BasicAuth(login=es_username, password=es_password) if es_username and es_password else None
    connector = aiohttp.TCPConnector(ssl=False)
    
    headers = {}
    if settings.CF_ACCESS_CLIENT_ID and settings.CF_ACCESS_CLIENT_SECRET:
        headers["CF-Access-Client-Id"] = settings.CF_ACCESS_CLIENT_ID
        headers["CF-Access-Client-Secret"] = settings.CF_ACCESS_CLIENT_SECRET
        print("🔒 已自動加入 Cloudflare Access 驗證標頭。")
    
    try:
        async with aiohttp.ClientSession(connector=connector, auth=auth, headers=headers) as session:
            # 1. 測試 GET /
            print("🚀 [1] 測試 GET /")
            async with session.get(es_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print("✅ 連線成功！叢集資訊：")
                    print(f"   - 叢集名稱: {data.get('cluster_name')}")
                    print(f"   - 版本號碼: {data.get('version', {}).get('number')}")
                    print(f"   - Lucene 版本: {data.get('version', {}).get('lucene_version')}")
                else:
                    text = await resp.text()
                    print(f"❌ 連線失敗 (HTTP {resp.status}): {text}")
                    return

            print("\n" + "="*50 + "\n")

            # 2. 測試 GET /_cat/plugins
            print("🚀 [2] 測試 GET /_cat/plugins?v&s=component&h=name,component,version,description")
            plugins_url = f"{es_url.rstrip('/')}/_cat/plugins?v&s=component&h=name,component,version,description"
            async with session.get(plugins_url) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    print("✅ 取得插件列表成功：")
                    print("-" * 50)
                    print(text.strip())
                    print("-" * 50)
                    
                    if "sudachi" in text.lower():
                        print("🎉 發現 'sudachi' 插件！適合進行日文進階 NLP 檢索。")
                    else:
                        print("⚠️ 警告：沒有找到 'sudachi' 插件。如果您打算使用進階日文檢索，請安裝 analysis-sudachi。")
                else:
                    text = await resp.text()
                    print(f"❌ 取得插件失敗 (HTTP {resp.status}): {text}")

    except Exception as e:
        print(f"❌ 連線過程中發生嚴重錯誤: {type(e).__name__} - {e}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_elasticsearch_connection())
