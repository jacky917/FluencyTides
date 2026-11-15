"""人脈卡只留一種卡片，並加上目標與三個新欄位（工作經歷計畫書 §8、§12.5）。

Keep a single person card type and add the goal and three new fields.

一次做完會讓 AnkiWeb 要求完整同步的變更，只完整同步一次：

1. 加欄位：``Goals_JSON``、``Common_Ground``、``Needs``、``Dislikes``，插在定義檔（``Person_Profile_Dark.json``）
   的位置（已經有的略過）。
2. 卡片模板「見面前複習」改名「人脈卡」（每個人都已經有這張卡）。
3. 刪掉「認人」模板：所有人的認人卡與它的複習紀錄一起刪掉（使用者同意複習紀錄可以丟，D15）；筆記與欄位內容不動。
4. 換成新的模板與 CSS（同 ``import_model.py``）。

預設只列出會做什麼（不寫入，dry-run），加 ``--apply`` 才執行。開始前會先同步，同步失敗就中止（其他裝置的修改要先
同步上來，完整同步時才不會遺失）。做過的步驟再執行一次會略過，可以重複執行。執行完要在 Anki 做一次完整同步
（選「上傳到 AnkiWeb」），其他裝置再下載。

Dry run by default; ``--apply`` performs the steps. Re-running skips the
steps already done. A full sync (upload to AnkiWeb) is needed afterwards.

用法 / Usage::

    python scripts/local_anki/Person_Profile_Dark/migrate_single_card.py
    python scripts/local_anki/Person_Profile_Dark/migrate_single_card.py --apply
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# 確保 sys.path 包含 backend 根目錄並載入 .env
backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
import scripts.common.env  # noqa: E402,F401

from app.infrastructure.anki.client import AnkiClient, AnkiConnectError  # noqa: E402
from app.infrastructure.anki.search import escape_search_value, note_type_term  # noqa: E402
from app.schemas.person_profile import PERSON_NOTE_TYPE  # noqa: E402
from scripts.local_anki.schema_builder import AnkiSchemaBuilder  # noqa: E402

MODEL_DIR = backend_dir / "app" / "anki_models"
#: 留下來、改名的模板（每個人都有）與新名字。The kept template and its new name.
KEEP_OLD, KEEP_NEW = "見面前複習", "人脈卡"
#: 要刪掉的模板。The template to remove.
DROP = "認人"


def target_fields() -> list[str]:
    """筆記類型定義檔（Person_Profile_Dark.json）的欄位，照順序。The fields of the model definition."""
    data = json.loads((MODEL_DIR / f"{PERSON_NOTE_TYPE}.json").read_text(encoding="utf-8"))
    return [str(name) for name in data["inOrderFields"]]


@dataclass
class Plan:
    """要做的步驟。The steps to perform."""

    add_fields: list[str] = field(default_factory=list)
    rename: bool = False
    remove: bool = False
    #: 會被刪掉的認人卡張數。Cards of the removed template.
    dropped_cards: int = 0
    #: 人數（筆記數）。The number of notes.
    notes: int = 0
    #: 沒辦法安全執行的原因。Why it cannot run safely.
    problems: list[str] = field(default_factory=list)


def _card_query(template: str) -> str:
    return f'{note_type_term(PERSON_NOTE_TYPE)} "card:{escape_search_value(template)}"'


async def make_plan(client: AnkiClient) -> Plan:
    """讀出現況，決定要做哪些步驟（唯讀）。Read the current state and decide the steps (read-only)."""
    plan = Plan()
    current_fields = await client.get_model_field_names(PERSON_NOTE_TYPE)
    wanted = target_fields()
    unknown = [name for name in wanted if name not in current_fields]
    plan.add_fields = unknown
    extra = [name for name in current_fields if name not in wanted]
    if extra:
        plan.problems.append(f"Anki 裡有定義檔沒有的欄位：{'、'.join(extra)}（請先確認定義檔）")
    # 新欄位照定義檔的位置插進去：既有欄位的相對順序要和定義檔相同，插完才會一致
    elif current_fields != [name for name in wanted if name in current_fields]:
        plan.problems.append("Anki 的欄位順序和定義檔不同，請先確認")

    templates = list(await client.get_model_templates(PERSON_NOTE_TYPE))
    notes = await client.find_notes(note_type_term(PERSON_NOTE_TYPE))
    plan.notes = len(notes)
    if KEEP_NEW in templates:
        pass  # 已經改過名
    elif KEEP_OLD in templates:
        plan.rename = True
        # 每個人都要有「見面前複習」卡：刪掉認人之後，沒有它的筆記會一張卡都不剩
        kept = await client.find_cards(_card_query(KEEP_OLD))
        if len(kept) != len(notes):
            plan.problems.append(f"「{KEEP_OLD}」卡有 {len(kept)} 張，人有 {len(notes)} 個：有人沒有這張卡，刪掉「{DROP}」會讓他沒有卡")
    else:
        plan.problems.append(f"找不到「{KEEP_OLD}」或「{KEEP_NEW}」模板：{'、'.join(templates)}")
    if DROP in templates:
        plan.remove = True
        plan.dropped_cards = len(await client.find_cards(_card_query(DROP)))
    return plan


def describe(plan: Plan) -> list[str]:
    """把計畫寫成給人看的幾行。The plan as readable lines."""
    lines = [f"人脈卡共 {plan.notes} 人"]
    lines.append(f"• 加欄位：{'、'.join(plan.add_fields)}" if plan.add_fields else "• 欄位：都已經有了，略過")
    lines.append(f"• 模板「{KEEP_OLD}」改名「{KEEP_NEW}」" if plan.rename else f"• 模板「{KEEP_NEW}」：已經改過名，略過")
    if plan.remove:
        lines.append(f"• 刪掉模板「{DROP}」：會刪掉 {plan.dropped_cards} 張認人卡與它們的複習紀錄（筆記內容不動）")
    else:
        lines.append(f"• 模板「{DROP}」：已經刪掉了，略過")
    lines.append("• 換成新的模板與 CSS")
    lines.extend(f"⚠️ {problem}" for problem in plan.problems)
    return lines


async def apply_plan(client: AnkiClient, plan: Plan) -> None:
    """照計畫執行：加欄位 → 改名 → 刪模板 → 換模板與 CSS。Perform the plan."""
    # 照定義檔的位置由前往後插：前面的插好了，後面的序號才對
    wanted = target_fields()
    for name in sorted(plan.add_fields, key=wanted.index):
        await client.add_model_field(PERSON_NOTE_TYPE, name, index=wanted.index(name))
        print(f"  ✓ 加欄位 {name}（第 {wanted.index(name) + 1} 個）")
    if plan.rename:
        await client.rename_model_template(PERSON_NOTE_TYPE, KEEP_OLD, KEEP_NEW)
        print(f"  ✓ 「{KEEP_OLD}」改名「{KEEP_NEW}」")
    if plan.remove:
        await client.remove_model_template(PERSON_NOTE_TYPE, DROP)
        print(f"  ✓ 刪掉「{DROP}」")
    await AnkiSchemaBuilder(client).update_model_templates_from_files(PERSON_NOTE_TYPE, MODEL_DIR)
    print("  ✓ 換成新的模板與 CSS")


async def main(apply: bool) -> None:
    """列出（或執行）遷移。List, or perform, the migration."""
    client = AnkiClient()
    try:
        try:
            await client.sync(raise_errors=True, force=True)
        except AnkiConnectError as e:
            print(f"❌ 同步失敗，中止（其他裝置的修改要先同步上來）：{e}")
            sys.exit(1)
        plan = await make_plan(client)
        for line in describe(plan):
            print(line)
        if plan.problems:
            print("❌ 有問題，沒有執行。")
            sys.exit(1)
        if not apply:
            print("（dry-run：沒有寫入。確認後加 --apply 執行）")
            return
        await apply_plan(client, plan)
        print("✅ 完成。請在 Anki 做一次完整同步（選「上傳到 AnkiWeb」），其他裝置再下載。")
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="實際執行（預設只列出）")
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(args.apply))
