"""單一專案全量清除核心（專案參數化）。

Project-parameterized full-cleanup core: delete all child cards, blank the
master-card JSON fields, purge project media and clear the project's rows
in the MySQL dedup log.

刪除子卡片、清空母卡片 JSON、清理資料庫與媒體；媒體刪除受跨專案保護——
只刪「本專案前綴、且**所有已註冊專案**皆未引用」的檔案。
Media deletion is cross-project safe: only files carrying this project's
prefix AND unreferenced by every registered project are removed.
"""

import asyncio
import logging

from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.database.corpus_database import (
    corpus_async_session_factory,
    dispose_corpus_engine,
)
from scripts.common.database.log_repository import GeneratedLogRepository
from scripts.local_anki.common.deletion.media_scan import (
    collect_required_media,
    guard_unreferenced,
)
from scripts.local_anki.common.deletion.profiles import ProjectProfile, build_registry

logger = logging.getLogger(__name__)


async def run_cleanup(
    profile: ProjectProfile,
    *,
    dry_run: bool,
    deck_name: str | None = None,
) -> None:
    """執行單一專案的全量清除。

    Run the full cleanup for one project.

    Args:
        profile: 專案描述子。Project profile.
        dry_run: True 時僅列出預計清理內容。Preview only when True.
        deck_name: 覆寫根牌組名稱；None 時使用 profile.root_deck。Root
            deck override; profile.root_deck when None.
    """
    base_deck_name = deck_name or profile.root_deck
    client = AnkiClient()

    try:
        if dry_run:
            print("\n========================================")
            print("🛡️  目前為 DRY RUN 模式，不會執行任何實際刪除  🛡️")
            print("   (若要正式執行，請加上 --execute)    ")
            print("========================================\n")

        print(f"🚀 準備清理根牌組: {base_deck_name} ({profile.display_name})")

        # 1. 蒐集需要刪除的 Context 和 Cloze 子卡牌
        notes_to_delete = []
        for subdeck in ["Context", "Cloze"]:
            deck = f"{base_deck_name}::{subdeck}"
            print(f"🔍 尋找 {deck} 的筆記...")
            notes = await client.find_notes(f'"deck:{deck}"')
            if notes:
                notes_to_delete.extend(notes)
                print(f"   找到 {len(notes)} 條來自 {deck} 的筆記。")
            else:
                print(f"   {deck} 中沒有找到筆記。")

        # 2. 蒐集需要清空 JSON 欄位的 Master 筆記
        master_deck = f"{base_deck_name}::Master"
        print(f"\n🔍 尋找 {master_deck} 的筆記...")
        master_notes = await client.find_notes(f'"deck:{master_deck}"')
        if master_notes:
            print(f"   找到 {len(master_notes)} 條 Master 筆記需要清空欄位。")
        else:
            print(f"   {master_deck} 中沒有找到筆記。")

        # 3. 蒐集需要刪除的媒體資源（跨專案保護）
        # 「他專案」= 註冊表中的其他專案；它們仍引用中的檔案一律不可刪。
        # 本專案的子卡與母卡 JSON 即將被清空，其引用不再計入保護。
        prefix = f"{profile.source_game}_"
        print(f"\n🔍 尋找前綴為 '{prefix}' 的媒體資源...")
        media_files = await client.get_media_files_names(f"{prefix}*")

        registry = build_registry()
        other_profiles = [
            p for p in registry.values() if p.project_key != profile.project_key
        ]
        protected = await collect_required_media(client, other_profiles)

        deletable_media = [m for m in media_files if m not in protected]
        protected_count = len(media_files) - len(deletable_media)
        if media_files:
            print(f"   找到 {len(media_files)} 個媒體資源，其中 {protected_count} 個仍被其他專案引用（受保護）。")
            print(f"   預計刪除 {len(deletable_media)} 個。")
        else:
            print(f"   沒有找到前綴為 '{prefix}' 的媒體資源。")

        # 4. 資料庫清理 (若有 DB)
        has_db = bool(corpus_async_session_factory)

        # 提示確認
        print("\n========================================")
        print("⚠️  即將執行的清理內容總結：")
        print(f"   - 刪除子卡片 (Context/Cloze): {len(notes_to_delete)} 條")
        print(f"   - 清空母卡片 JSON 欄位 {list(profile.master_json_fields)}: {len(master_notes)} 條")
        print(f"   - 刪除媒體資源 ({prefix}*，排除他專案引用): {len(deletable_media)} 個")
        print(f"   - 清空 MySQL generated_sentences_log (project={profile.project_key}): {'是' if has_db else '否 (無 DB 連線)'}")
        print("========================================\n")

        if not dry_run:
            if not notes_to_delete and not master_notes and not deletable_media and not has_db:
                print("✨ 沒有需要清理的項目。")
                return

            print("🚨 警告：此操作不可逆！將會徹底刪除上述所有內容。")
            user_input = await asyncio.to_thread(input, "確定要繼續執行清除嗎？ [y/N]: ")
            if user_input.strip().lower() != 'y':
                print("❌ 已取消操作。")
                return

            print("\n🔥 開始執行清理...")

            if notes_to_delete:
                print(f"🗑️ 正在刪除 {len(notes_to_delete)} 條子卡片筆記...")
                await client.delete_notes(notes_to_delete)

            if master_notes:
                print(f"📝 正在清空 {len(master_notes)} 條 Master 筆記的 JSON 欄位...")
                empty_fields = {name: "[]" for name in profile.master_json_fields}
                for note_id in master_notes:
                    await client.update_note_fields(note_id, empty_fields)

            if deletable_media:
                # 最後防線：本專案卡片已刪/清空後，逐檔對整個集合（不限
                # 筆記類型）全文搜尋，仍被任何卡片引用的一律攔下。
                print(f"🛡️ 正在對 {len(deletable_media)} 個媒體做全集合引用交叉驗證...")
                confirmed, blocked = await guard_unreferenced(client, deletable_media)
                for fname, ref_count in blocked.items():
                    print(f"   ⚠️ 攔下 {fname}（仍被 {ref_count} 張卡片引用，不刪除）")
                print(f"🗑️ 正在刪除 {len(confirmed)} 個媒體資源...")
                for media_file in confirmed:
                    await client.delete_media_file(media_file)

            if has_db:
                print(f"🧹 正在清理 MySQL generated_sentences_log (project={profile.project_key})...")
                async with corpus_async_session_factory() as session:
                    log_repo = GeneratedLogRepository()
                    await log_repo.clear_all_records(
                        session, project=profile.project_key, hard_delete=True
                    )
                    # 硬刪除後收斂 AUTO_INCREMENT（改用按專案 DELETE 之後，
                    # 不再有 TRUNCATE 的自動重置，需明確補上）
                    await log_repo.reset_auto_increment(session)

            print("✅ 所有清理作業已順利完成！")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ 發生錯誤: {e}")
    finally:
        await client.close()
        await dispose_corpus_engine()
