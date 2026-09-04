"""查詢日文分詞詞典——後端與本機各用哪一本,並對帳。

Query which Japanese tokenizer dictionary is in use, on the backend and
locally, and reconcile the two.

呼叫 ``GET /api/v1/jp/tokenizer/dictionary`` 取後端行程載入的詞典,同時在
本行程解析一次(``app.infrastructure.utils.jp_tokenizer.resolve_dictionary``),
兩邊並排顯示。

為什麼要兩邊都看:**生卡腳本是在自己的行程裡分詞的**,後端只負責產內容。
所以決定卡片正確與否的是「本機」那一本;後端那一本只影響
``NLPProcessor.normalize_verb`` 等 API 內部用途。兩者只有跑在同一個容器內
才必然一致。

為什麼這件事重要:完整版 ``unidic``(3.1.0)與回退的 ``unidic-lite``(2.1.2)
對「連用形名詞化」這類邊界判定不同,而切換是靜默的、不會報錯,只會表現在
產出的卡片上。2026-09-04 有一批 490 張核心動詞子卡在容器內以 lite 產出,
其中 6 張在完整版下不成立(例:``先輩の答え`` 的 ``答え`` lite 判動詞、
完整版判名詞,因而生出一張句中根本沒有目標動詞的卡);同時所有 dry-run
基線都量在完整版上,與正式跑的環境對不上,數字失去意義。

Usage:
    python query_jp_tokenizer.py

    # 只看本機,不連後端(例:在容器內確認自己這一本)
    python query_jp_tokenizer.py --local-only
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import httpx

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[2]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.core.config import settings
from app.infrastructure.utils.jp_tokenizer import KIND_UNIDIC, resolve_dictionary

DOWNLOAD_HINT = "python -m unidic download"


def _print_dictionary(title: str, kind: str, version: str, dicdir: str, preferred: bool) -> None:
    """輸出一本詞典的資訊。Print one dictionary's details.

    Args:
        title: 區塊標題(後端 / 本機)。Section title.
        kind: 詞典種類。Dictionary kind.
        version: 版本字串。Version string.
        dicdir: 詞典目錄。Dictionary directory.
        preferred: 是否為偏好的完整版。Whether it is the preferred one.
    """
    print(f"\n========== {title} ==========")
    print(f"{'✅' if preferred else '⚠️'} 詞典  : {kind} {version}")
    print(f"📁 路徑  : {dicdir}")
    if not preferred:
        print(f"   → 跑在回退詞典上;要對齊完整版請在此環境執行 `{DOWNLOAD_HINT}`")


async def _fetch_backend() -> dict | None:
    """向後端查詢它載入的詞典。Ask the backend which dictionary it loaded.

    Returns:
        dict | None: 端點回應;連線失敗或後端版本過舊時為 None。
            The endpoint payload, or None when unavailable.
    """
    base_url = getattr(settings, "SCRIPTS_API_BASE_URL", "http://127.0.0.1:8000")
    url = f"{base_url.rstrip('/')}/api/v1/jp/tokenizer/dictionary"

    headers: dict[str, str] = {}
    if settings.CF_ACCESS_CLIENT_ID and settings.CF_ACCESS_CLIENT_SECRET:
        headers["CF-Access-Client-Id"] = settings.CF_ACCESS_CLIENT_ID
        headers["CF-Access-Client-Secret"] = settings.CF_ACCESS_CLIENT_SECRET

    print(f"🔍 查詢後端: {url}")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        print(f"❌ 無法連線後端: {e}")
        return None

    if resp.status_code == 404:
        print("❌ 後端回 404:此後端版本尚未包含分詞詞典端點,需重新部署映像。")
        return None
    if resp.status_code != 200:
        print(f"❌ 後端回應異常 ({resp.status_code}): {resp.text[:200]}")
        return None
    return resp.json()


async def main() -> None:
    """腳本主入口:顯示後端與本機的詞典並對帳。

    Entry point: show both dictionaries and reconcile them.
    """
    parser = argparse.ArgumentParser(description="查詢日文分詞詞典(後端 + 本機)")
    parser.add_argument(
        "--local-only", action="store_true",
        help="只解析本機詞典,不連後端",
    )
    args = parser.parse_args()

    # resolve_dictionary 以 logging 輸出結果(完整版 INFO / 回退 WARNING),
    # 這裡開 INFO 讓那行訊息與腳本輸出一起出現。
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 只留本腳本的輸出

    backend = None if args.local_only else await _fetch_backend()

    local = resolve_dictionary()
    if backend:
        _print_dictionary(
            "後端行程", backend.get("kind", "?"), backend.get("version", "?"),
            backend.get("dicdir", "?"), bool(backend.get("is_preferred")),
        )
    _print_dictionary("本機行程(生卡腳本用的就是這本)", local.kind, local.version,
                      local.dicdir, local.is_preferred)

    print("\n========== 對帳 ==========")
    if local.kind != KIND_UNIDIC:
        print("🚨 本機跑在回退詞典上——生成的卡片會與完整版環境的 dry-run 基線對不上。")
        print(f"   請執行 `{DOWNLOAD_HINT}` 後重跑(完整版約 1GB)。")
    else:
        print("✅ 本機用完整版 unidic,與 dry-run 基線一致。")

    if backend:
        if backend.get("kind") != local.kind:
            print("⚠️ 後端與本機用的詞典不同種。")
            print(f"   後端: {backend.get('kind')} {backend.get('version')}")
            print(f"   本機: {local.kind} {local.version}")
            print("   生卡由本機分詞決定,後端那本只影響 API 內部的 lemma 正規化;")
            print("   兩者不同不會直接生出錯卡,但代表兩邊環境沒對齊,值得補齊。")
        else:
            print("✅ 後端與本機使用同一種詞典。")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
