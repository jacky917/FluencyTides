"""讀取 core_verbs.json 批次建立 JP_CoreVerb 母卡片（全程冪等，可重複執行）。

Read core_verbs.json and batch-create JP_CoreVerb master cards; the
whole process is idempotent (safe to rerun): existing note_ids are
verified via a fast path, missing cards are deduplicated by
furigana-stripped verb form, and new note_ids are written back.

行為：

1. 讀取動詞清單 `core_verbs.json`（``--file`` 可覆寫，預設同目錄）。
2. 快速跳過（note_id 快路徑）：條目已有 ``note_id`` → 以 ``notesInfo``
   驗證該卡仍存在 → 直接跳過（零搜尋成本）。卡已被手動刪除 →
   清掉失效 note_id，改走慢路徑。
3. 慢路徑（無 note_id 或已失效）：撈出牌組內全部母卡，以「去標音」後的
   動詞乾淨形逐一比對查重——欄位內存的是 furigana 標音格式
   （如 ``見[み]る``），無法用 Anki 原生欄位搜尋直接比對乾淨形。
   - 已存在 → 取得其 note_id 回寫，跳過建卡。
   - 不存在 → 建卡（Word / Word_Data_JSON="[]" / Card_ID），回寫新 note_id。
4. 回寫時機：全部處理完後一次性寫回 ``core_verbs.json``（保留原有欄位與
   順序，僅補 note_id）；``--dry-run`` 不寫檔、只列印將發生的動作。
5. 摘要輸出：``新建 N / 快速跳過 K / 補綁 note_id M / 失效清除 R``。

使用方式：
    python scripts/local_anki/JP_CoreVerb/create_master_card.py [--dry-run]
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.core.config import settings
from scripts.common.verb_lemma import canonical_verb_lemma
from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.utils.id_generator import generate_unique_card_id

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# 母卡模型名（與 app/anki_models/JP_CoreVerb_Master_Dark.json 一致）
MASTER_MODEL_NAME = "JP_CoreVerb_Master_Dark"

# 預設清單路徑：與本腳本同目錄的 core_verbs.json
DEFAULT_JSON_PATH = Path(__file__).resolve().parent / "core_verbs.json"


def strip_furigana(word: str) -> str:
    """去除 furigana 標音，取得動詞乾淨形。

    Strip furigana annotations to get the plain verb form.

    Anki 的標音格式如 ``見[み]る`` 或 `` 掛[か]ける``（漢字前可能帶空格
    分隔），去除 ``[...]`` 與空白後即為乾淨形（``見る`` / ``掛ける``）。

    Args:
        word: furigana 標音格式的動詞字串。Verb string in furigana notation.

    Returns:
        str: 去標音、去空白後的動詞乾淨形。
            Plain verb form with furigana and whitespace removed.
    """
    return canonical_verb_lemma(word)


def load_core_verbs(json_path: Path) -> list[dict]:
    """讀取並驗證 core_verbs.json 動詞清單。

    Load and validate the core_verbs.json verb list.

    Args:
        json_path: core_verbs.json 的路徑。Path to core_verbs.json.

    Returns:
        list[dict]: 動詞條目列表（每筆至少含 ``word``，可能含 ``note_id``）。
            List of verb entries (each has ``word``, optionally ``note_id``).

    Raises:
        FileNotFoundError: 檔案不存在時。When the file does not exist.
        ValueError: JSON 格式錯誤或頂層不是列表時。
            When the JSON is invalid or the top level is not a list.
    """
    if not json_path.is_file():
        raise FileNotFoundError(f"找不到動詞清單檔案: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"無效的 JSON 檔案 {json_path}: {e}")

    if not isinstance(data, list):
        raise ValueError(f"{json_path} 頂層必須是陣列。")
    return data


async def verify_existing_note_ids(client: AnkiClient, note_ids: list[int]) -> set[int]:
    """以 notesInfo 批次驗證一組 note_id 是否仍存在於 Anki 中。

    Batch-verify via notesInfo which note_ids still exist in Anki.

    Args:
        client: AnkiConnect 客戶端實例。AnkiConnect client instance.
        note_ids: 待驗證的 Anki 原生 note id 列表。Native Anki note ids to verify.

    Returns:
        set[int]: 仍然存在的 note_id 集合。Set of note_ids that still exist.

    Raises:
        AnkiConnectError: AnkiConnect 請求失敗時。When the AnkiConnect request fails.
    """
    if not note_ids:
        return set()
    infos = await client.get_notes_info(notes=note_ids)
    return {info.noteId for info in infos}


async def fetch_deck_word_map(client: AnkiClient, deck: str) -> dict[str, int]:
    """撈出牌組內全部母卡，建立「乾淨形動詞 → note_id」查重映射。

    Fetch all master cards in the deck and build a deduplication map
    from furigana-stripped verb form to note_id.

    因為 ``Word`` 欄位存的是 furigana 標音格式（如 ``見[み]る``），無法用
    Anki 原生欄位搜尋直接比對乾淨形，只能全量撈取後在本地去標音比對。

    Args:
        client: AnkiConnect 客戶端實例。AnkiConnect client instance.
        deck: 母卡牌組名（可含 ``::`` 階層）。
            Master-card deck name (may contain ``::`` hierarchy).

    Returns:
        dict[str, int]: 去標音動詞 → Anki note_id 的映射。
            Map of furigana-stripped verb to Anki note_id.

    Raises:
        AnkiConnectError: AnkiConnect 請求失敗時。When the AnkiConnect request fails.
    """
    query = f'"deck:{deck}" "note:{MASTER_MODEL_NAME}"'
    note_ids = await client.find_notes(query)
    if not note_ids:
        return {}

    word_map: dict[str, int] = {}
    infos = await client.get_notes_info(notes=note_ids)
    for info in infos:
        word_field = info.fields.get("Word", {})
        raw_word = str(word_field.get("value", "")).strip()
        if not raw_word:
            continue
        clean = strip_furigana(raw_word)
        # 同一乾淨形若有多張母卡（理論上不該發生），保留最先找到的那張
        word_map.setdefault(clean, info.noteId)
    return word_map


async def create_master_note(client: AnkiClient, deck: str, word: str) -> int | None:
    """在指定牌組建立一張核心動詞母卡。

    Create one core-verb master card in the given deck.

    Args:
        client: AnkiConnect 客戶端實例。AnkiConnect client instance.
        deck: 目標牌組名。Target deck name.
        word: furigana 標音格式的核心動詞（直接存入 ``Word`` 欄位）。
            Core verb in furigana notation (stored into ``Word`` as-is).

    Returns:
        int | None: 建卡成功時回傳 Anki note_id；失敗時回傳 None。
            Anki note_id on success; None on failure.

    Raises:
        AnkiConnectError: AnkiConnect 請求失敗時。When the AnkiConnect request fails.
    """
    master_card_id = generate_unique_card_id(prefix="cv-m")
    note = {
        "deckName": deck,
        "modelName": MASTER_MODEL_NAME,
        "fields": {
            "Word": word,
            "Word_Data_JSON": "[]",
            "Card_ID": master_card_id,
        },
        "options": {
            "allowDuplicate": True
        },
        "tags": ["CoreVerb", "ManualCreated"]
    }
    results = await client._invoke("addNotes", notes=[note])
    if results and results[0]:
        logger.info(f"   ✨ Card_ID: {master_card_id}")
        return int(results[0])
    return None


async def process_verbs(
    client: AnkiClient,
    entries: list[dict],
    deck: str,
    dry_run: bool,
) -> tuple[dict[str, int], bool]:
    """逐條處理動詞清單：快路徑驗證、慢路徑查重、必要時建卡並回寫 note_id。

    Process verb entries one by one: fast-path note_id verification,
    slow-path deck deduplication, card creation when needed, and
    in-place note_id rebinding.

    Args:
        client: AnkiConnect 客戶端實例。AnkiConnect client instance.
        entries: core_verbs.json 的條目列表（就地修改 note_id）。
            Entries from core_verbs.json (note_id mutated in place).
        deck: 母卡牌組名。Master-card deck name.
        dry_run: True 時不建卡、不改動 Anki，只列印將發生的動作
                 （但仍會就地清除失效 note_id 供列印說明；不會寫檔）。
                 If True, print planned actions without touching Anki.

    Returns:
        tuple[dict[str, int], bool]:
            - 摘要計數（created / fast_skipped / rebound / invalid_cleared）。
              Summary counters.
            - 條目是否有變動（供呼叫端決定是否需要回寫 json）。
              Whether entries changed (caller decides to write back).

    Raises:
        AnkiConnectError: AnkiConnect 請求失敗時。When the AnkiConnect request fails.
    """
    counters = {"created": 0, "fast_skipped": 0, "rebound": 0, "invalid_cleared": 0}
    changed = False

    # --- 快路徑：一次性批次驗證所有已綁定的 note_id ---
    bound_ids = [int(e["note_id"]) for e in entries if e.get("note_id")]
    alive_ids = await verify_existing_note_ids(client, bound_ids)

    # 慢路徑的牌組查重映射採惰性載入（全部條目都走快路徑時零搜尋成本）
    word_map: dict[str, int] | None = None

    for entry in entries:
        word = str(entry.get("word", "")).strip()
        if not word:
            logger.warning("⚠️ 略過缺少 word 欄位的條目: %s", entry)
            continue
        clean_word = strip_furigana(word)

        # 快路徑：note_id 仍有效 → 直接跳過
        note_id = entry.get("note_id")
        if note_id:
            if int(note_id) in alive_ids:
                counters["fast_skipped"] += 1
                logger.info(f"⏩ [{clean_word}] note_id {note_id} 驗證存在，快速跳過。")
                continue
            # 卡已被手動刪除 → 清除失效 note_id，落入慢路徑
            counters["invalid_cleared"] += 1
            changed = True
            logger.info(f"🧹 [{clean_word}] note_id {note_id} 已失效，清除後改走慢路徑。")
            entry.pop("note_id", None)

        # 慢路徑：以去標音乾淨形在牌組內全量比對查重
        if word_map is None:
            logger.info(f"🔎 載入牌組「{deck}」的既有母卡建立查重映射...")
            word_map = await fetch_deck_word_map(client, deck)
            logger.info(f"   共 {len(word_map)} 張既有母卡。")

        existing_nid = word_map.get(clean_word)
        if existing_nid:
            counters["rebound"] += 1
            changed = True
            entry["note_id"] = existing_nid
            logger.info(f"🔗 [{clean_word}] 牌組中已存在 (note_id: {existing_nid})，補綁 note_id。")
            continue

        # 不存在 → 建卡
        if dry_run:
            counters["created"] += 1
            logger.info(f"📝 [dry-run] [{clean_word}] 將於「{deck}」建立母卡並回寫 note_id。")
            continue

        logger.info(f"🚀 [{clean_word}] 準備寫入 Anki (Word: {word})")
        new_nid = await create_master_note(client, deck, word)
        if new_nid:
            counters["created"] += 1
            changed = True
            entry["note_id"] = new_nid
            # 同步進映射，避免清單內重複條目在同一輪重複建卡
            word_map[clean_word] = new_nid
            logger.info(f"✅ [{clean_word}] 母卡建立成功！ Note ID: {new_nid}")
        else:
            logger.error(f"❌ [{clean_word}] 建立失敗，可能是 AnkiConnect 問題或牌組/模型不存在。")

    return counters, changed


def write_back(json_path: Path, entries: list[dict]) -> None:
    """把補上 note_id 的條目一次性寫回 core_verbs.json。

    Write the entries (with newly bound note_ids) back to
    core_verbs.json in a single pass.

    以 ``json.dump``（ensure_ascii=False、縮排 2）覆寫；dict 天然保留插入
    順序，原有欄位與條目順序不會被打亂。

    Args:
        json_path: core_verbs.json 的路徑。Path to core_verbs.json.
        entries: 處理後的動詞條目列表。Processed verb entries.
    """
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
        f.write("\n")


async def main() -> None:
    """腳本進入點：解析參數、批次處理、回寫檔案並輸出摘要。

    Script entry point: parse args, process entries in batch, write
    the file back, and print the summary.
    """
    parser = argparse.ArgumentParser(description="讀取 core_verbs.json 批次建立核心動詞母卡片（冪等）")
    parser.add_argument(
        "-f", "--file",
        type=Path,
        default=DEFAULT_JSON_PATH,
        help=f"動詞清單 JSON 路徑 (預設: {DEFAULT_JSON_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列印將發生的動作，不建卡、不寫回檔案",
    )
    args = parser.parse_args()

    # 牌組名從 settings 讀取；設定尚未存在時退回計劃書預設值
    deck = getattr(settings, "JP_CORE_VERB_MASTER_DECK", "日本語::核心動詞::Master")

    entries = load_core_verbs(args.file)
    logger.info(f"📋 讀入 {len(entries)} 個動詞條目 (來源: {args.file})")
    logger.info(f"🎴 目標牌組: {deck}{'  [dry-run 模式]' if args.dry_run else ''}")

    client = AnkiClient()
    try:
        counters, changed = await process_verbs(client, entries, deck, args.dry_run)

        if args.dry_run:
            logger.info("💤 dry-run 模式：不寫回 core_verbs.json。")
        elif changed:
            write_back(args.file, entries)
            logger.info(f"💾 已回寫 {args.file}")
        else:
            logger.info("✨ 無任何變動，不需回寫檔案。")

        logger.info(
            "📊 摘要: 新建 %d / 快速跳過 %d / 補綁 note_id %d / 失效清除 %d",
            counters["created"],
            counters["fast_skipped"],
            counters["rebound"],
            counters["invalid_cleared"],
        )
    except Exception as e:
        logger.error(f"❌ 發生未預期的錯誤: {e}")
        raise
    finally:
        await client.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
