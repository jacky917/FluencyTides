"""跨專案媒體引用掃描。

Cross-project media reference scanner.

媒體檔（語音/頭像）以遊戲代號為前綴，且兩個卡片專案共用同一批來源檔，
因此「哪些媒體可刪」必須以**所有已註冊專案**的引用聯集來判斷，
單一專案的視角會把他專案引用中的檔案誤判為孤兒。
Media files share one game prefix across projects, so "deletable" must be
computed against the union of references from ALL registered projects;
a single project's view would misclassify the other project's media as
orphans.
"""

import logging
from collections.abc import Iterable

from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from scripts.local_anki.common.deletion.profiles import ProjectProfile, _field_value

logger = logging.getLogger(__name__)


async def get_all_notes(client: AnkiClient, query: str) -> list[dict]:
    """批次取得所有符合查詢條件的 Anki 筆記資訊（原始 dict）。

    Fetch raw info dicts for all Anki notes matching the query in batches.

    為避免單次請求筆記數量過多導致 AnkiConnect 超時，
    採用 500 筆一組的分頁策略。
    Uses 500-note pagination to avoid AnkiConnect timeouts.

    Args:
        client: AnkiConnect 客戶端。AnkiConnect client instance.
        query: Anki 搜尋查詢語句。Anki search query string.

    Returns:
        list[dict]: 所有符合條件筆記的原始資訊字典。Raw info dicts for
        every matching note.
    """
    notes = await client._invoke('findNotes', query=query)
    if not notes:
        return []

    all_info: list[dict] = []
    chunk_size = 500
    for i in range(0, len(notes), chunk_size):
        chunk = notes[i:i + chunk_size]
        info = await client._invoke('notesInfo', notes=chunk)
        all_info.extend(info)
    return all_info


def _add_if_real(media: set[str], filename: str) -> None:
    """把有效的媒體檔名加入集合（略過空值與 "none" 佔位）。

    Add a real media filename to the set, skipping empties and the
    "none" placeholder.

    Args:
        media: 目標集合。Target set.
        filename: 待加入的檔名。Filename candidate.
    """
    if filename and filename != "none":
        media.add(filename)


def collect_required_media_from_notes(
    profile: ProjectProfile,
    master_notes: Iterable[dict],
    cloze_notes: Iterable[dict],
    context_notes: Iterable[dict],
) -> set[str]:
    """從已載入的筆記中蒐集單一專案引用中的媒體檔名。

    Collect the media filenames referenced by one project from
    already-loaded notes.

    掃描三個來源：母卡 JSON 欄位的 audio/avatar、Cloze 卡的 Audio/Avatar
    欄位、Context 卡 Dialog_JSON 的每一輪對話。
    Scans master JSON fields (audio/avatar), cloze Audio/Avatar fields,
    and every turn of each context card's Dialog_JSON.

    Args:
        profile: 專案描述子。Project profile.
        master_notes: 母卡原始資訊。Raw master note infos.
        cloze_notes: Cloze 卡原始資訊。Raw cloze note infos.
        context_notes: Context 卡原始資訊（呼叫端須先做好專案歸屬分流）。
            Raw context note infos (caller is responsible for project
            attribution).

    Returns:
        set[str]: 引用中的媒體檔名集合。Set of referenced media filenames.
    """
    required: set[str] = set()

    for info in master_notes:
        fields = info.get("fields", {})
        for json_field_name in profile.master_json_fields:
            raw_str = _field_value(fields, json_field_name)
            for item in AnkiJsonFieldManager.parse_field_string(raw_str):
                if isinstance(item, dict):
                    _add_if_real(required, item.get("audio", ""))
                    _add_if_real(required, item.get("avatar", ""))

    for info in cloze_notes:
        fields = info.get("fields", {})
        _add_if_real(required, _field_value(fields, "Audio"))
        _add_if_real(required, _field_value(fields, "Avatar"))

    for info in context_notes:
        fields = info.get("fields", {})
        dialog_str = _field_value(fields, "Dialog_JSON")
        if dialog_str:
            for turn in AnkiJsonFieldManager.parse_field_string(dialog_str):
                if isinstance(turn, dict):
                    _add_if_real(required, turn.get("audio", ""))
                    _add_if_real(required, turn.get("avatar", ""))

    return required


async def guard_unreferenced(
    client: AnkiClient,
    filenames: Iterable[str],
) -> tuple[list[str], dict[str, int]]:
    """刪除前的最後防線：對整個 Anki 集合做全文搜尋，確認檔案零引用。

    Final pre-deletion guard: full-text search the ENTIRE Anki collection
    to confirm each file is referenced by zero notes.

    為什麼需要這層？孤兒判定的保護集合只掃「已註冊筆記類型」的欄位；
    若有未註冊的筆記類型（或手動建立的卡片）引用了同前綴的檔案，
    掃描不會看見。Anki 的欄位搜尋是搜原始文字，JSON 欄位內的裸檔名
    也搜得到，因此可作為與判定邏輯獨立的交叉驗證。
    The orphan protection set only covers registered note models; a note
    of an unregistered model could still reference a same-prefix file.
    Anki's field search matches raw text (bare filenames inside JSON
    included), making it an independent cross-check.

    Args:
        client: AnkiConnect 客戶端。AnkiConnect client.
        filenames: 待刪除的候選檔名。Deletion candidates.

    Returns:
        tuple[list[str], dict[str, int]]: (確認零引用可刪的檔名,
        {仍被引用的檔名: 引用卡片數})。(Confirmed-deletable filenames,
        {still-referenced filename: referencing note count}).
    """
    deletable: list[str] = []
    still_referenced: dict[str, int] = {}
    for fname in filenames:
        notes = await client.find_notes(f'"{fname}"')
        if notes:
            still_referenced[fname] = len(notes)
        else:
            deletable.append(fname)
    if still_referenced:
        logger.warning(
            f"   ⚠️ 全集合交叉驗證攔下 {len(still_referenced)} 個仍被引用的檔案（不刪除）"
        )
    return deletable, still_referenced


async def collect_required_media(
    client: AnkiClient,
    profiles: Iterable[ProjectProfile],
) -> set[str]:
    """蒐集多個專案引用中媒體檔名的聯集。

    Collect the union of referenced media filenames across projects.

    Context 模型可能被多個專案共用；為了媒體保護的目的不需要做專案歸屬
    分流——只要**任何**卡片還引用著，該檔就不可刪，因此共用模型的
    Context 卡全量掃描即可（同名模型只掃一次）。
    A shared context model needs no per-project attribution here: a file
    is protected if ANY card still references it, so shared-context notes
    are scanned once in full.

    Args:
        client: AnkiConnect 客戶端。AnkiConnect client.
        profiles: 要納入保護的專案清單。Profiles to include.

    Returns:
        set[str]: 引用中的媒體檔名聯集。Union of referenced filenames.
    """
    required: set[str] = set()
    scanned_context_models: set[str] = set()

    for profile in profiles:
        master_notes = await get_all_notes(client, f'note:"{profile.master_model}"')
        cloze_notes = await get_all_notes(client, f'note:"{profile.cloze_model}"')
        if profile.context_model not in scanned_context_models:
            context_notes = await get_all_notes(client, f'note:"{profile.context_model}"')
            scanned_context_models.add(profile.context_model)
        else:
            context_notes = []

        found = collect_required_media_from_notes(
            profile, master_notes, cloze_notes, context_notes
        )
        logger.info(
            f"   => {profile.display_name}: 引用中媒體 {len(found)} 個"
        )
        required |= found

    return required
