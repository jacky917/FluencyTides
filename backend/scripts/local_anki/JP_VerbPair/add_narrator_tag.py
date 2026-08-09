"""
為現有的無說話者（旁白）克漏字卡片補上 Narrator 標籤。

Add the Narrator tag to existing cloze cards that have no speaker (narration).

調用範例：
1. 預覽會更新哪些卡片（DRY-RUN 模式）
   python add_narrator_tag.py

2. 實際執行更新寫入標籤
   python add_narrator_tag.py --execute
"""

import json
import urllib.request
import argparse

ANKI_URL = "http://127.0.0.1:8765"

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
    """腳本主入口：篩選旁白卡片並補上 Narrator 標籤。

    Script entry point: filter narration cards and add the Narrator tag.
    """
    parser = argparse.ArgumentParser(description="更新現有克漏字卡片，若無說話者與頭像則打上 Narrator 標籤。")
    parser.add_argument("--execute", action="store_true", help="實際執行標籤寫入 (預設為 DRY-RUN 模式)")
    args = parser.parse_args()

    dry_run = not args.execute

    print("🔍 正在搜尋 JP_VerbPair_Cloze_Dark 卡片...")
    success, note_ids = invoke("findNotes", query="note:JP_VerbPair_Cloze_Dark")
    if not success or not note_ids:
        print("沒有找到任何卡片。")
        return

    print(f"📦 找到 {len(note_ids)} 張卡片，正在獲取詳細資訊...")
    success, notes_info = invoke("notesInfo", notes=note_ids)
    if not success or not notes_info:
        print("獲取詳細資訊失敗。")
        return
    
    to_update = []

    for note in notes_info:
        note_id = note["noteId"]
        fields = note["fields"]
        tags = note["tags"]
        
        speaker = fields.get("Speaker", {}).get("value", "").strip()
        avatar = fields.get("Avatar", {}).get("value", "").strip()

        # 判斷是否為旁白
        if speaker in ("-", "", "none") and avatar == "none":
            if "Narrator" not in tags:
                cloze_sentence = fields.get("Cloze_Sentence", {}).get("value", "")
                to_update.append((note_id, cloze_sentence))

    print(f"🎯 共有 {len(to_update)} 張卡片符合「無說話者且無頭像」條件且尚未標記 Narrator。")

    if not to_update:
        print("✅ 沒有需要更新的卡片。")
        return

    if dry_run:
        print("\n--- 🛠️ DRY-RUN 模式 ---")
        print("以下卡片將會被加上 Narrator 標籤：")
        for nid, sentence in to_update:
            print(f" - [ID: {nid}] {sentence}")
        print("\n💡 提示: 若要實際更新，請加上 --execute 參數執行腳本。")
    else:
        print("\n--- 🚀 執行模式 ---")
        update_ids = [nid for nid, _ in to_update]
        tag_str = "Narrator"
        print(f"正在為 {len(update_ids)} 張卡片添加 '{tag_str}' 標籤...")
        
        success, _ = invoke("addTags", notes=update_ids, tags=tag_str)
        if success:
            print("✅ 更新成功！")
        else:
            print("❌ 更新發生錯誤。")

if __name__ == "__main__":
    main()
