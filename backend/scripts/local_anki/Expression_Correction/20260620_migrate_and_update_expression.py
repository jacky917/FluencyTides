"""20260620 Expression_Master_Dark 樣板更新與 Context 欄位無損遷移腳本。

Migration script (2026-06-20) that force-updates the Expression_Master_Dark
templates/CSS and losslessly migrates legacy plain-text Context fields into
the new JSON bubble format.
"""

import asyncio
import json
import re
import urllib.request
import sys
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[4]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.infrastructure.anki.client import AnkiClient
from app.core.config import settings

# ==========================================
# 1. 更新 Anki 模板 (HTML/CSS)
# ==========================================
def invoke_anki_connect(action, **params):
    """同步呼叫 AnkiConnect API 並回傳結果。

    Synchronously call the AnkiConnect API and return the result.

    Args:
        action: AnkiConnect 動作名稱。AnkiConnect action name.
        **params: 動作參數。Parameters for the action.

    Returns:
        API 回傳的 result 內容。The result payload returned by the API.

    Raises:
        Exception: 回應格式錯誤或 API 回報錯誤時。On malformed responses or API errors.
    """
    requestJson = json.dumps({"action": action, "version": 6, "params": params}).encode('utf-8')
    response = json.load(urllib.request.urlopen(urllib.request.Request(settings.ANKI_CONNECT_URL, requestJson)))
    if len(response) != 2: raise Exception('response has an unexpected number of fields')
    if 'error' not in response: raise Exception('response is missing required error field')
    if 'result' not in response: raise Exception('response is missing required result field')
    if response['error'] is not None: raise Exception(response['error'])
    return response['result']

def update_templates_and_css():
    """強制更新 Expression_Master_Dark 的樣板與 CSS。

    Force-update the Expression_Master_Dark templates and CSS in Anki
    from the local HTML/CSS template files.
    """
    model_name = "Expression_Master_Dark"
    
    print("🎨 [1/2] 正在強制更新 Anki 筆記模板與 CSS...")
    
    # 動態取得 backend 目錄路徑 (scripts/local_anki/Expression_Correction/ -> scripts/local_anki/ -> scripts/ -> backend/)
    script_dir = Path(__file__).resolve().parent
    backend_dir = script_dir.parent.parent.parent
    models_dir = backend_dir / "app" / "anki_models"
    
    with open(models_dir / f"{model_name}_front.html", "r", encoding="utf-8") as f:
        front = f.read()
    with open(models_dir / f"{model_name}_back.html", "r", encoding="utf-8") as f:
        back = f.read()
    with open(models_dir / f"{model_name}_style.css", "r", encoding="utf-8") as f:
        css = f.read()
        
    invoke_anki_connect('updateModelTemplates', model={"name": model_name, "templates": {"Card 1": {"Front": front, "Back": back}}})
    invoke_anki_connect('updateModelStyling', model={"name": model_name, "css": css})
    print("✅ 模板與 CSS 更新完成！")

# ==========================================
# 2. 無損遷移舊卡片資料
# ==========================================
async def migrate_context_to_json():
    """將舊卡片的純文字 Context 欄位無損遷移為 JSON 陣列格式。

    Losslessly migrate legacy plain-text Context fields into the JSON
    array format, splitting on long dash separators.
    """
    print("\n🔄 [2/2] 正在檢查舊卡片並進行無損遷移...")
    client = AnkiClient()
    
    note_ids = await client.find_notes("note:Expression_Master_Dark")
    print(f"🔍 尋找到 {len(note_ids)} 筆 Expression_Master_Dark 記錄。")
    
    if not note_ids:
        return
        
    notes_info = await client.get_notes_info(note_ids)
    
    updated_count = 0
    for info in notes_info:
        note_id = info.noteId
        fields = info.fields
        
        context_data = fields.get("Context", {}).get("value", "")
        
        # 檢查是否已經是 JSON 格式（簡單判斷是否以 '[' 開頭）
        if context_data.strip().startswith("["):
            continue
            
        print(f"  ➜ 正在遷移 Note ID: {note_id} ...")
        
        # 將純文字利用 ーーーーー 切分為陣列
        context_parts = [p.strip() for p in re.split(r'ー{5,}', context_data) if p.strip()]
        
        if not context_parts:
            new_context_str = "[]"
        else:
            new_context_str = json.dumps([{"text": p} for p in context_parts], ensure_ascii=False)
            
        # 僅更新 Context 欄位，無損學習歷程
        await client.update_note_fields(note_id, {"Context": new_context_str})
        updated_count += 1
        
    print(f"✅ 資料遷移完成！共成功更新 {updated_count} 筆舊記錄。")

async def main():
    """腳本主入口：依序執行樣板同步與卡片資料遷移。

    Script entry point: run the template sync followed by the card migration.
    """
    print("🚀 啟動 20260620 Expression_Master_Dark 對話氣泡更新與資料遷移腳本\n" + "="*60)
    # 1. 執行模板同步
    update_templates_and_css()
    # 2. 執行卡片升級
    await migrate_context_to_json()
    print("\n🎉 所有更新流程順利結束！請打開 Anki 享受全新的對話氣泡體驗！")

if __name__ == "__main__":
    asyncio.run(main())
