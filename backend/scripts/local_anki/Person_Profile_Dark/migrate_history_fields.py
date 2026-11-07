"""把人脈卡會變的欄位（興趣、職位、現居地等）從文字轉成清單（登錄日計畫書 §4.6、D2）。

Convert the person card's history fields from text to JSON lists.

每個欄位（``PERSON_HISTORY_FIELDS``）的原有文字轉成 ``[{value, added, ended}]``，切法同網頁建卡表單：
興趣、飲食喜好、語言以逗號、頓號、換行切成多筆，其他整段一筆。登錄日留空（卡片顯示「日期不明」），
全部是有效的。公司與職稱合成一筆職位 ``{company, title, added, ended}`` 放在 ``Company``，``Title``
清空（欄位保留不刪）。已經是 JSON 清單的欄位不動，所以可以重複執行。

轉不好的（例如一段文字其實是好幾件事）請在網頁的 JSON 欄位編輯器手動修（D2：不保證無損）。不合格式的
內容（例如親密度寫「很熟」）不轉、看起來是壞掉的 JSON 的欄位不動，都會列出來請你手動處理。
預設只列出會怎麼轉（不寫入），加 ``--apply`` 才寫入並同步；開始前同步失敗就中止（不在過時的資料上寫入）。

Text becomes one entry (or several, for the comma-split fields) with an
unknown added date; Company and Title merge into one position. Fields
that are already lists are left alone. Dry run by default; ``--apply``
writes and syncs.

用法 / Usage::

    python scripts/local_anki/Person_Profile_Dark/migrate_history_fields.py
    python scripts/local_anki/Person_Profile_Dark/migrate_history_fields.py --apply
"""

import argparse
import asyncio
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# 確保 sys.path 包含 backend 根目錄並載入 .env
backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
import scripts.common.env  # noqa: E402,F401

from app.core.exceptions import AnkiFieldCorruptedError  # noqa: E402
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError  # noqa: E402
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager  # noqa: E402
from app.infrastructure.anki.search import note_type_term  # noqa: E402
from app.schemas.person_profile import PERSON_NOTE_TYPE, html_to_text, legacy_to_history  # noqa: E402


def _is_list(raw: str) -> bool:
    """欄位已經是 JSON 清單（空的也算，不用轉）。Whether the field is already a list."""
    try:
        AnkiJsonFieldManager.parse_field_string(raw)
    except AnkiFieldCorruptedError:
        return False
    return True


async def main(apply: bool) -> None:
    """找出還是文字的欄位並轉換。Find and convert the text fields."""
    client = AnkiClient()
    try:
        try:
            await client.sync(raise_errors=True, force=True)
        except AnkiConnectError as e:
            print(f"❌ 開始前同步失敗，為避免在過時的資料上寫入已中止：{e}")
            return
        note_ids = await client.find_notes(note_type_term(PERSON_NOTE_TYPE))
        notes = await client.get_notes_info(notes=note_ids) if note_ids else []
        print(f"▶ 檢查 {len(notes)} 張人脈卡" + ("" if apply else "（試跑，不寫入）"))
        changed = 0
        attention: list[str] = []
        for note in notes:
            fields = {name: str(field.get("value", "")) for name, field in note.fields.items()}
            result = legacy_to_history(fields, _is_list)
            label = f"{note.noteId} {html_to_text(fields.get('Display_Name', ''))}"
            for name in result.skipped:
                attention.append(f"{label}：{name} 看起來是壞掉的 JSON，沒有轉換，請在網頁或 Anki 手動修")
            for name, text in result.dropped:
                attention.append(f"{label}：{name} 的「{text}」不合格式，沒有轉進去，請手動補")
            if result.leftover_title:
                attention.append(f"{label}：職位已經是清單，但 Title 還有「{result.leftover_title}」（不再顯示），請手動併進職位")
            if not result.changes:
                continue
            changed += 1
            print(f"\n  {label}")
            for name, value in result.changes.items():
                before = html_to_text(fields.get(name, "")).replace("\n", " / ")
                after = html_to_text(value) or "（空白）"
                print(f"    {name}: {before!r}\n      → {after}")
            if apply:
                await client.update_note_fields(note.noteId, result.changes)
        if attention:
            print("\n⚠️ 要手動處理：")
            for line in attention:
                print(f"  {line}")
        print(f"\n共 {changed} 張要轉換。" + ("" if apply else "加上 --apply 才會寫入。"))
        if apply and changed:
            # 寫入前的同步有 10 秒防抖：最後這次一定要強制，否則可能被跳過
            try:
                await client.sync(raise_errors=True, force=True)
                print("✅ 已寫入並同步")
            except AnkiConnectError as e:
                print(f"⚠️ 已寫入本機 Anki，但同步失敗，請手動同步：{e}")
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="實際寫入（預設只列出）")
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(args.apply))
