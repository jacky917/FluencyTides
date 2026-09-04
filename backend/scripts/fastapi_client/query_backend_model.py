"""查詢後端當前使用的 LLM 模型(呼叫 config API,不讀後端 .env)。

Query which LLM model the backend is currently using via the config API
(never by reading the backend's .env).

呼叫 ``GET /api/v1/config``(計畫
docs/archive/runtime_config_service_FEAT_2026-08-29.md §3.5 的唯讀切片),
顯示:
- runtime 對帳區塊:``llm_label``(後端活 client 算好的顯示標籤,
  即生成時會寫進 Anki tag 的值)、``llm_provider``、``anki_connect_url``
- claude-code 環境診斷:CLI 路徑/版本、effort、token 設定狀態、認證實測,
  以及登入帳號的訂閱方案(``max`` / ``pro``;CLI 不區分 5x 與 20x)
- 白名單設定清單(當前值/可選項/是否觸發重建)
- 順帶對帳:後端的 anki_connect_url 與**本機** .env 的 ANKI_CONNECT_URL
  不一致時發出醒目警告(兩邊指向不同 Anki 會造成卡片與媒體分裂,
  詳見計畫 §3.5)。

為什麼問 API 而不是讀 .env:後端與腳本的 .env 是兩份檔案(2026-08-28
曾因此錯標 190 筆 DB 紀錄),唯一可信的是後端「執行期」的實際狀態。

Usage:
    # 預設含真實認證探測(對 claude-code 實打一次最小 haiku 請求)
    python query_backend_model.py

    # 只看靜態診斷,不消耗訂閱請求
    python query_backend_model.py --no-check-auth
"""

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[2]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.core.config import settings


async def main() -> None:
    """腳本主入口:呼叫 config API 並輸出後端模型與對帳資訊。

    Script entry point: call the config API and print the backend model
    and reconciliation info.
    """
    parser = argparse.ArgumentParser(description="查詢後端當前 LLM 模型與 claude-code 環境診斷")
    parser.add_argument(
        "--no-check-auth", action="store_true",
        help="跳過真實認證探測(預設會對 claude-code 實打一次最小 haiku 請求驗證 token)"
    )
    args = parser.parse_args()
    check_auth = not args.no_check_auth

    base_url = getattr(settings, "SCRIPTS_API_BASE_URL", "http://127.0.0.1:8000")
    url = f"{base_url.rstrip('/')}/api/v1/config"
    params = {"check_auth": "true"} if check_auth else {}

    headers: dict[str, str] = {}
    if settings.CF_ACCESS_CLIENT_ID and settings.CF_ACCESS_CLIENT_SECRET:
        headers["CF-Access-Client-Id"] = settings.CF_ACCESS_CLIENT_ID
        headers["CF-Access-Client-Secret"] = settings.CF_ACCESS_CLIENT_SECRET

    print(f"🔍 查詢後端: {url}" + ("(含真實認證探測,需數秒~數十秒)" if check_auth else ""))
    try:
        async with httpx.AsyncClient(timeout=180 if check_auth else 10) as client:
            resp = await client.get(url, headers=headers, params=params)
    except httpx.HTTPError as e:
        print(f"❌ 無法連線後端: {e}")
        return

    if resp.status_code == 404:
        print("❌ 後端回 404:此後端版本尚未包含 config API。")
        print("   需部署含 PR #14 之後程式碼的映像(容器記得 --force-recreate)。")
        return
    if resp.status_code != 200:
        print(f"❌ 後端回應異常 ({resp.status_code}): {resp.text[:200]}")
        return

    data = resp.json()
    runtime = data.get("runtime", {})

    print("\n========== 後端執行期狀態 ==========")
    label = runtime.get("llm_label")
    if label:
        print(f"🤖 當前模型標籤 : {label}")
        print("   (= 生成時寫入 Anki tag 的值;寫 DB 請取生成回應的 llm_model)")
    else:
        print("🤖 當前模型標籤 : (LLM 客戶端未初始化——後端啟動時 LLM 初始化失敗?)")
    print(f"🔌 LLM Provider  : {runtime.get('llm_provider')}")
    print(f"🃏 後端 Anki 端點: {runtime.get('anki_connect_url')}")

    # claude-code 環境診斷(provider 非 claude-code 時後端回 null)
    cc = runtime.get("claude_code")
    if cc is not None:
        print("\n========== claude-code 環境診斷 ==========")
        ok = "✅" if cc.get("client_initialized") else "❌"
        print(f"{ok} LLM client 初始化: {cc.get('client_initialized')}")
        print(f"📦 CLI 路徑        : {cc.get('cli_path') or '(無)'}")
        if cc.get("cli_version"):
            print(f"🏷️ CLI 版本        : {cc['cli_version']}  ← 實際執行 claude --version 取得")
        else:
            print(f"❌ CLI 版本探測失敗: {cc.get('cli_version_error') or '(未知原因)'}")
        print(f"🎚️ Effort          : {cc.get('effort')}")

        # 訂閱方案:CLI 只回到 max / pro 這一層,不區分 Max 5x 與 20x
        account = cc.get("account") or {}
        if account.get("status") == "ok":
            plan = account.get("subscription_type")
            method = account.get("auth_method")
            via = " / ".join(x for x in (method, account.get("api_provider")) if x)
            state = "已登入" if account.get("logged_in") else "🚨 未登入"
            print(f"💳 訂閱方案        : {plan or '(CLI 未回報)'}({state}{'，' + via if via else ''})")
            if plan:
                print("   (CLI 粒度僅到 max/pro,不含 5x/20x 倍率)")
            elif method == "oauth_token":
                print("   (注入 token 認證時 CLI 不回報方案;走落盤憑證的桌機模式才有)")
        elif account:
            print(f"⚪ 訂閱方案        : 探測不到——{account.get('detail')}")
        token_set = cc.get("oauth_token_configured")
        mode = "已設定(headless/容器模式,注入 token 認證)" if token_set \
            else "未設定(桌機模式,走落盤憑證)"
        print(f"🔑 OAuth token     : {mode}")
        if token_set and cc.get("oauth_token_format_ok") is False:
            print(f"🚨 token 格式異常  : {cc.get('oauth_token_format_error')}")
            print("   → 請重新完整複製 `claude setup-token` 的輸出(應為一段連續字串)")

        auth = cc.get("auth_check") or {}
        status = auth.get("status")
        if status == "ok":
            print(f"✅ 認證實測        : 通過({auth.get('detail')})")
        elif status == "failed":
            print(f"🚨 認證實測        : 失敗——{auth.get('detail')}")
        else:
            print("⚪ 認證實測        : 未執行(--no-check-auth 或後端版本不支援)")

        if cc.get("client_initialized") and cc.get("cli_version") and status == "ok":
            print("✅ claude-code 環境完全就緒(binary 可執行 + 認證實測通過)")
        elif cc.get("client_initialized") and cc.get("cli_version") and status != "failed":
            print("🟡 binary 可執行,但認證未實測——確定要驗證請不帶 --no-check-auth 重跑")

    # 對帳:後端與本機的 Anki 端點必須指向同一台
    local_anki = settings.ANKI_CONNECT_URL
    backend_anki = runtime.get("anki_connect_url")
    if backend_anki and local_anki and backend_anki.rstrip("/") != local_anki.rstrip("/"):
        print("\n🚨 警告:後端與本機的 ANKI_CONNECT_URL 不一致!")
        print(f"   後端: {backend_anki}")
        print(f"   本機: {local_anki}")
        print("   兩邊指向不同 Anki 時,後端建卡與腳本傳媒體會分裂,請先對齊再生成。")

    configs = data.get("configs", [])
    if configs:
        print("\n========== 可動態修改的設定 ==========")
        for entry in configs:
            options = entry.get("options")
            options_str = " / ".join(options) if options else "(不限)"
            # requires_rebuild 是資訊性標記:改「值」時後端會自動重建對應
            # 元件(LLM client 等),與容器/映像無關,呼叫端無需任何動作
            rebuild_mark = "  🔁(改值時後端自動重建 client)" if entry.get("requires_rebuild") else ""
            print(f"  {entry['key']} = {entry['current_value']}{rebuild_mark}")
            print(f"      可選: {options_str}")
    else:
        print("\n(白名單為空:.env 未設任何 MODIFY_* 變數)")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
