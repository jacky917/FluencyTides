"""替 Anki app 建立、還沒有 Card_ID 的人脈卡補上 Card_ID 與 TG_Bot（人脈卡計畫書 R7）。

Backfill Card_ID and TG_Bot for person cards created in the Anki app.

沒有 ``Card_ID`` 時卡面不顯示 TG 按鈕，TG 的深連結也找不到這個人。TG 用 ``/p`` 找到他時會自動補上；
這支腳本一次補齊。預設只列出會改哪些筆記（不寫入），加 ``--apply`` 才寫入。

Without a Card_ID the card shows no Telegram buttons; this script fills
them in. Dry run by default; ``--apply`` writes.

用法 / Usage::

    python scripts/local_anki/Person_Profile_Dark/backfill_card_ids.py
    python scripts/local_anki/Person_Profile_Dark/backfill_card_ids.py --apply
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

from app.core.config import settings  # noqa: E402
from app.infrastructure.anki.client import AnkiClient  # noqa: E402
from app.infrastructure.anki.search import note_type_term  # noqa: E402
from app.schemas.person_profile import PERSON_NOTE_TYPE  # noqa: E402
from app.services.task_handlers.person_profile_handler import new_card_id  # noqa: E402


async def main(apply: bool) -> None:
    """找出沒有 Card_ID（或 TG_Bot）的人脈卡並補上。Find and fill the missing IDs."""
    bot = (settings.TG_BOT_USERNAME or "").lstrip("@")
    client = AnkiClient()
    try:
        await client.sync(raise_errors=False)
        note_ids = await client.find_notes(f'{note_type_term(PERSON_NOTE_TYPE)} ("Card_ID:" OR "TG_Bot:")')
        notes = await client.get_notes_info(notes=note_ids) if note_ids else []
        print(f"▶ 找到 {len(notes)} 張缺 Card_ID 或 TG_Bot 的人脈卡" + ("" if apply else "（試跑，不寫入）"))
        for note in notes:
            fields = {name: str(field.get("value", "")) for name, field in note.fields.items()}
            changes: dict[str, str] = {}
            if not fields.get("Card_ID", "").strip():
                changes["Card_ID"] = new_card_id()
            if not fields.get("TG_Bot", "").strip() and bot:
                changes["TG_Bot"] = bot
            if not changes:
                continue
            print(f"  {note.noteId} {fields.get('Display_Name', '')}: {changes}")
            if apply:
                await client.update_note_fields(note.noteId, changes)
        if apply and notes:
            await client.sync(raise_errors=False)
        print("✅ 完成" if apply else "加上 --apply 才會寫入。")
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="實際寫入（預設只列出）")
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(args.apply))
