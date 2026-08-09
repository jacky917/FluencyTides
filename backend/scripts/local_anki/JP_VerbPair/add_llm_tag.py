"""
為現有的子卡片（Context、Cloze）補上預設的 LLM 模型標籤。

Add the default LLM model tag to existing child cards (Context and Cloze).

調用範例：
1. 預覽會更新幾張卡片（DRY-RUN 模式）
   python add_llm_tag.py

2. 實際執行更新寫入標籤
   python add_llm_tag.py --execute
"""

import json
import urllib.request
import argparse

ANKI_URL = "http://127.0.0.1:8765"
TARGET_TAG = "LLM::gemini-3.1-pro-preview"

def invoke(action, **params):
    """呼叫 AnkiConnect API 並回傳結果。

    Call the AnkiConnect API and return the result.

    Args:
        action: AnkiConnect 動作名稱。AnkiConnect action name.
        **params: 動作參數。Parameters for the action.

    Returns:
        (成功與否, 結果) 的元組。A (success, result) tuple.
    """
    request_data = json.dumps({
        "action": action,
        "version": 6,
        "params": params
    }).encode("utf-8")
    
    try:
        req = urllib.request.Request(ANKI_URL, data=request_data)
        with urllib.request.urlopen(req) as response:
            response_data = json.loads(response.read())
            if response_data.get("error"):
                raise Exception(response_data["error"])
            return True, response_data.get("result")
    except Exception as e:
        print(f"❌ AnkiConnect API 請求失敗 ({action}): {e}")
        return False, None

def main():
    """腳本主入口：搜尋缺標籤的子卡片並補上 LLM 標籤。

    Script entry point: find child cards missing the tag and add it.
    """
    parser = argparse.ArgumentParser(description=f"為現有子卡片補上 {TARGET_TAG} 標籤。")
    parser.add_argument("--execute", action="store_true", help="實際執行標籤寫入 (預設為 DRY-RUN 模式)")
    args = parser.parse_args()

    dry_run = not args.execute

    query = f"(note:JP_VerbPair_Cloze_Dark OR note:JP_Context_Dark) -tag:{TARGET_TAG}"
    print(f"🔍 正在搜尋遺漏 {TARGET_TAG} 標籤的子卡片...")
    success, note_ids = invoke("findNotes", query=query)
    
    if not success or not note_ids:
        print("✅ 所有卡片都已經有該標籤了，沒有需要更新的卡片。")
        return

    print(f"🎯 共有 {len(note_ids)} 張卡片尚未標記 {TARGET_TAG}。")

    if dry_run:
        print("\n--- 🛠️ DRY-RUN 模式 ---")
        print(f"這 {len(note_ids)} 張卡片將會被加上 {TARGET_TAG} 標籤。")
        print("\n💡 提示: 若要實際更新，請加上 --execute 參數執行腳本。")
    else:
        print("\n--- 🚀 執行模式 ---")
        print(f"正在為 {len(note_ids)} 張卡片添加 '{TARGET_TAG}' 標籤...")
        
        success, _ = invoke("addTags", notes=note_ids, tags=TARGET_TAG)
        if success:
            print("✅ 更新成功！")
        else:
            print("❌ 更新發生錯誤。")

if __name__ == "__main__":
    main()
