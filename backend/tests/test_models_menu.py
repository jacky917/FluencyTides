"""TG ``/setconfig`` 選單的測試（能力路由計畫 §8.5；前身為模型路由計畫 D7 的 ``/models``）。

Tests for the stateless ``/setconfig`` menu: callback data size, version and
arity checks, the layered capability → provider → model → effort flow
(auto-skipping model and effort), pagination, admin gating, stale buttons,
database-down behaviour, error reporting, and full click-throughs that write
to a real temporary table.
"""

import asyncio
import html
import re
from contextlib import nullcontext
from unittest import mock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, User
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.bot.handlers import models_menu
from app.bot.handlers.models_menu import (
    CALLBACK_DATA_LIMIT,
    CLOSE_DATA,
    NO_EFFORT,
    NO_MODEL,
    Action,
    MenuContext,
    MenuKey,
    StaleMenuError,
    build_context,
    callback,
    parse_callback,
    render_models,
    render_providers,
    resolve_selection,
)
from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.infrastructure.database.models import CapabilityRouteSetting
from app.infrastructure.llm import routing
from app.infrastructure.llm.capabilities import CAPABILITIES, CAPABILITY_ORDER, ProviderOption, list_providers
from app.infrastructure.llm.claude_code_client import ClaudeCodeLLMClient
from app.infrastructure.llm.endpoints import ModelOption
from app.services import capability_route_service

ADMIN_ID = 42
KEY = "test.menu"
STALE = "選單已過期，請重新輸入 /setconfig。"

_PROVIDERS = {
    "llm": (
        ProviderOption("claude-code", "claude-code", (
            ModelOption("opus", ("low", "high")),
            ModelOption("sonnet", ("high",)),
            ModelOption("haiku"),
        )),
    ),
    "audio_eval": tuple(list_providers("audio_eval")),
    "audio_llm": (),
    "stt": (),
}
_KEYS = [MenuKey("*", CAPABILITY_ORDER, CAPABILITIES), MenuKey(KEY, ("llm",), frozenset({"llm"}))]


@pytest.fixture(autouse=True)
def _admin_and_key(declare_route_key):
    """管理員與測試用途；錄音評分預設 stt_diff、STT 已設定（不必有音訊金鑰，清單不會因此標 ⚠️）。"""
    declare_route_key(KEY, "選單測試")
    with mock.patch.object(settings, "TG_ADMIN_CHAT_ID", ADMIN_ID),          mock.patch.object(settings, "AUDIO_EVALUATOR_PROVIDER", "stt_diff"),          mock.patch.object(settings, "STT_SERVER_URL", "http://stt.test/v1"):
        yield


def _ctx(keys=None, providers=None) -> MenuContext:
    return build_context(keys or _KEYS, providers or _PROVIDERS)


# ----------------------------------------------------------------------
# callback data、版本與參數（Callback data, versioning and arity）
# ----------------------------------------------------------------------


def test_callback_data_fits_telegram_limit():
    """最長的動作（儲存）帶大序號也不超過 64 位元組。"""
    data = callback(_ctx(), Action.SAVE, 9999, 9, 9999, 9999, 99)
    assert len(data.encode("utf-8")) <= CALLBACK_DATA_LIMIT


@pytest.mark.parametrize(
    "data",
    ["lr:k:000000:1", "setcfg_val:x:y", "lr:zz:{v}", "lr:k:{v}", "lr:s:{v}:1:0:0:0", "lr:k:{v}:1:2"],
    ids=["other-version", "other-prefix", "unknown-action", "missing-arg", "short-save", "extra-arg"],
)
def test_parse_rejects_outdated_or_malformed(data):
    """版本不同、前綴不符、動作未知、參數個數不對：一律視為過期。"""
    ctx = _ctx()
    with pytest.raises(StaleMenuError):
        parse_callback(data.format(v=ctx.version), ctx)


def test_parse_accepts_valid_data():
    ctx = _ctx()
    assert parse_callback(callback(ctx, Action.DETAIL, 1), ctx) == (Action.DETAIL, ["1"])
    assert parse_callback(callback(ctx, Action.CAPABILITY, 1, 0), ctx) == (Action.CAPABILITY, ["1", "0"])


def test_version_is_derived_and_changes_with_lists():
    """版本由清單算出；白名單、用途清單或策略限縮改變時跟著變，舊按鈕因此失效。"""
    base = _ctx()
    changed_models = _ctx(providers={
        **_PROVIDERS, "llm": (ProviderOption("claude-code", "claude-code", (ModelOption("opus", ("low",)),)),),
    })
    more_keys = _ctx(keys=[*_KEYS, MenuKey("zz", ("llm",))])
    restricted = _ctx(keys=[_KEYS[0], MenuKey(KEY, ("audio_eval",), frozenset({"audio_eval"}), frozenset({"stt_diff"}))])
    assert len({base.version, changed_models.version, more_keys.version, restricted.version}) == 4


def test_context_requires_global_key_first():
    """用途清單第一項必須是全域 *。"""
    with pytest.raises(ValueError):
        _ctx(keys=[MenuKey(KEY, ("llm",), frozenset({"llm"}))])


def test_model_buttons_skip_effort_when_not_needed():
    """多個 effort 去選 effort；只有一個自動選上；不使用 effort 直接到確認。"""
    ctx = _ctx()
    _, markup = render_models(ctx, 1, 0, 0)
    actions = [row[0].callback_data for row in markup.inline_keyboard[:3]]
    assert actions == [
        callback(ctx, Action.EFFORTS, 1, 0, 0, 0),
        callback(ctx, Action.CONFIRM, 1, 0, 0, 1, 0),
        callback(ctx, Action.CONFIRM, 1, 0, 0, 2, NO_EFFORT),
    ]


def test_strategy_buttons_skip_model_and_effort():
    """評分策略不選模型：選了直接到確認（模型、effort 記號都是「不選」）。"""
    ctx = _ctx()
    _, markup = render_providers(ctx, 0, 1)  # 全域 * 的 audio_eval
    actions = [row[0].callback_data for row in markup.inline_keyboard[:3]]
    assert actions == [callback(ctx, Action.CONFIRM, 0, 1, index, NO_MODEL, NO_EFFORT) for index in range(3)]
    provider, model, effort = resolve_selection(ctx, 0, 1, 1, NO_MODEL, NO_EFFORT)
    assert (provider.id, model, effort) == ("stt_diff", "", None)


def test_strategies_follow_the_keys_restriction():
    """用途鍵限縮了策略時，只列允許的策略（序號依過濾後的清單）。"""
    key = MenuKey("shadow", ("audio_eval",), frozenset({"audio_eval"}), frozenset({"stt_diff"}))
    ctx = _ctx(keys=[_KEYS[0], key])
    assert [option.id for option in ctx.providers_for(1, 0)] == ["stt_diff"]
    with pytest.raises(StaleMenuError):
        ctx.provider_at(1, 0, 1)


@pytest.mark.parametrize(
    ("model_token", "effort_token"),
    [("0", NO_EFFORT), ("2", "0"), ("0", "5"), ("9", "0"), ("0", "x"), (NO_MODEL, NO_EFFORT)],
    ids=[
        "missing-effort", "effort-on-plain-model", "effort-out-of-range", "model-out-of-range", "garbage",
        "model-required",
    ],
)
def test_invalid_selection_is_stale(model_token, effort_token):
    """序號或記號對不上白名單：視為過期，不會存錯的組合。"""
    with pytest.raises(StaleMenuError):
        resolve_selection(_ctx(), 1, 0, 0, model_token, effort_token)


def test_model_token_on_a_strategy_is_stale():
    """策略沒有模型：帶了模型序號就是過期的按鈕。"""
    with pytest.raises(StaleMenuError):
        resolve_selection(_ctx(), 0, 1, 0, "0", NO_EFFORT)


# ----------------------------------------------------------------------
# Handler 流程（Handler flow, real temporary table）
# ----------------------------------------------------------------------


def _message(user_id=ADMIN_ID):
    message = mock.MagicMock()
    message.from_user.id = user_id
    message.answer = mock.AsyncMock()
    return message


def _callback_query(data, user_id=ADMIN_ID):
    query = mock.MagicMock()
    query.from_user.id = user_id
    query.data = data
    query.message.edit_text = mock.AsyncMock()
    query.answer = mock.AsyncMock()
    return query


def _buttons(markup) -> dict[str, str]:
    return {button.text: button.callback_data for row in markup.inline_keyboard for button in row}


def _button_starting(markup, prefix: str) -> str:
    found = [data for text, data in _buttons(markup).items() if text.startswith(prefix)]
    assert found, f"沒有以 {prefix!r} 開頭的按鈕：{list(_buttons(markup))}"
    return found[0]


def _click(data: str):
    """按一個按鈕，回傳 (畫面文字, 鍵盤, 回呼查詢)；沒有更新畫面時前兩者為 None。"""
    query = _callback_query(data)
    asyncio.run(models_menu.handle_menu(query))
    if query.message.edit_text.await_args is None:
        return None, None, query
    text = query.message.edit_text.await_args.args[0]
    return text, query.message.edit_text.await_args.kwargs["reply_markup"], query


def _open_menu():
    message = _message()
    asyncio.run(models_menu.command_setconfig_handler(message))
    return message.answer.await_args.args[0], message.answer.await_args.kwargs.get("reply_markup")


def _stored_rows(session_factory):
    async def fetch():
        async with session_factory() as session:
            return list((await session.execute(select(CapabilityRouteSetting))).scalars())

    return asyncio.run(fetch())


def _insert(session_factory, **fields) -> None:
    async def insert():
        async with session_factory() as session:
            session.add(CapabilityRouteSetting(updated_by="test", capability="llm", **fields))
            await session.commit()

    asyncio.run(insert())


def _db_down():
    error = OperationalError("SELECT ...", {}, Exception("database is locked"))
    return mock.patch.object(routing, "_fetch_choices", mock.AsyncMock(side_effect=error))


def _to_capability(key_prefix: str, capability_prefix: str = "文字 LLM"):
    """從 /setconfig 點到某用途的某能力詳情，回傳 (畫面文字, 鍵盤)。"""
    _, markup = _open_menu()
    _, markup, _ = _click(_button_starting(markup, key_prefix))
    text, markup, _ = _click(_button_starting(markup, capability_prefix))
    return text, markup


def _to_model_screen(model_prefix: str):
    """從 /setconfig 點到指定模型之後的畫面。"""
    _, markup = _to_capability(f"{KEY}：")
    _, markup, _ = _click(_button_starting(markup, "⚙️ 設定"))
    _, markup, _ = _click(_button_starting(markup, "claude-code"))
    return _click(_button_starting(markup, model_prefix))


def test_full_flow_saves_choice(llm_route_db, llm_whitelist):
    """/setconfig → 用途 → 能力 → 設定 → 端點 → 模型 → effort → 確認 → 儲存，寫入資料表並回報新設定。"""
    _, markup, _ = _to_model_screen("opus")
    text, markup, _ = _click(_button_starting(markup, "low"))
    assert "(claude-code)opus@low" in text
    text, _, query = _click(_button_starting(markup, "✅ 儲存"))

    assert "已儲存" in text and "(claude-code)opus@low" in text
    query.answer.assert_awaited_once_with()
    [row] = _stored_rows(llm_route_db)
    assert (row.route_key, row.capability, row.provider, row.model, row.effort, row.updated_by) == (
        KEY, "llm", "claude-code", "opus", "low", f"tg:{ADMIN_ID}",
    )


def test_strategy_flow_saves_without_model(llm_route_db, llm_whitelist):
    """錄音評分：選策略後直接確認、儲存；存的模型是 NULL，下一次評分即改用新策略。"""
    _, markup = _to_capability("recording_eval：", "評分策略")
    _, markup, _ = _click(_button_starting(markup, "⚙️ 設定"))
    text, markup, _ = _click(_button_starting(markup, "stt_diff"))
    assert "評分策略" in text and "stt_diff" in text
    text, _, query = _click(_button_starting(markup, "✅ 儲存"))

    assert "已儲存" in text and "stt_diff" in text
    query.answer.assert_awaited_once_with()
    [row] = _stored_rows(llm_route_db)
    assert (row.route_key, row.capability, row.provider, row.model) == ("recording_eval", "audio_eval", "stt_diff", None)
    assert asyncio.run(routing.resolve("recording_eval.speaking_trilingual", "audio_eval")).provider == "stt_diff"


def test_branch_keys_are_indented_and_list_their_capabilities(llm_route_db, llm_whitelist):
    """分支鍵縮排在流程鍵之下；詳情列出它宣告的每個能力。"""
    _, markup = _open_menu()
    labels = list(_buttons(markup))
    flow = next(index for index, label in enumerate(labels) if label.startswith("recording_eval："))
    assert labels[flow + 1].startswith("　└ recording_eval.speaking_trilingual：")
    text, markup, _ = _click(_button_starting(markup, "　└ recording_eval.speaking_trilingual："))
    assert all(title in text for title in ("文字 LLM", "評分策略", "聽音檔的模型", "語音轉文字"))
    assert "（來源：.env）" in text


def test_single_effort_model_skips_effort_and_back_returns_to_models(llm_route_db, llm_whitelist):
    """只有一個 effort 的模型直接到確認（自動選上），「返回」回到選模型而不是選 effort。"""
    text, markup, _ = _to_model_screen("sonnet")
    assert "(claude-code)sonnet@high" in text
    _, back_markup, _ = _click(_button_starting(markup, "⬅️ 返回"))

    assert "haiku" in _buttons(back_markup)


def test_restore_default_deletes_row(llm_route_db, llm_whitelist):
    """恢復預設：刪掉該列並顯示退回後生效的設定。"""
    asyncio.run(capability_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))
    text, markup = _to_capability(f"{KEY}：")
    assert "本用途的設定" in text
    text, _, _ = _click(_button_starting(markup, "↩️ 恢復預設"))

    assert "已恢復預設" in text
    assert _stored_rows(llm_route_db) == []


def test_orphan_delete_then_back_to_list(llm_route_db, llm_whitelist):
    """孤兒鍵：能力詳情沒有「設定」只有「恢復預設」；刪除後清單變短，「返回清單」仍可用。"""
    _insert(llm_route_db, route_key="zz.orphan", provider="yinli", model="m1")
    _, markup = _to_capability("⚠️ zz.orphan")
    assert not any(text.startswith("⚙️ 設定") for text in _buttons(markup))
    text, markup, _ = _click(_button_starting(markup, "↩️ 恢復預設"))
    assert "已刪除" in text and "zz.orphan" in text
    text, markup, query = _click(_button_starting(markup, "⬅️ 返回清單"))

    assert "工具設定" in text
    query.answer.assert_awaited_once_with()
    assert not any("zz.orphan" in label for label in _buttons(markup))
    assert _stored_rows(llm_route_db) == []


def test_pagination(llm_route_db, llm_whitelist, declare_route_key):
    """超過一頁時可翻頁；後面頁的按鈕打開的是正確的用途（序號是絕對序號）；頁碼越界夾到最後一頁。"""
    for index in range(10):
        declare_route_key(f"zpage.{index:02d}", f"第 {index} 個")
    text, markup = _open_menu()
    assert "第 1 / " in text
    pages = int(re.search(r"第 1 / (\d+) 頁", text).group(1))
    for _ in range(pages):  # 一路翻到 zpage.09 所在的頁
        if any(label.startswith("zpage.09：") for label in _buttons(markup)):
            break
        text, markup, _ = _click(_button_starting(markup, "下一頁"))
    detail, _, _ = _click(_button_starting(markup, "zpage.09："))
    assert "第 9 個" in detail

    ctx = asyncio.run(models_menu.load_context())
    text, _, _ = _click(callback(ctx, Action.LIST, 99))
    assert f"第 {pages} / {pages} 頁" in text


@pytest.mark.parametrize(
    "build",
    [
        lambda ctx: f"lr:zz:{ctx.version}",
        lambda ctx: f"lr:k:{ctx.version}",
        lambda ctx: f"lr:k:{ctx.version}:abc",
        lambda ctx: callback(ctx, Action.DETAIL, 99),
        lambda ctx: callback(ctx, Action.SAVE, 1, 0, 0, 0, NO_EFFORT),
    ],
    ids=["unknown-action", "missing-arg", "non-integer", "key-out-of-range", "effort-token-mismatch"],
)
def test_malformed_buttons_are_stale_and_do_nothing(llm_route_db, llm_whitelist, build):
    """版本正確但內容壞掉的按鈕：提示過期，不更新畫面、不寫入。"""
    ctx = asyncio.run(models_menu.load_context())
    text, _, query = _click(build(ctx))

    assert text is None
    query.answer.assert_awaited_once_with(STALE, show_alert=True)
    assert _stored_rows(llm_route_db) == []


def test_rejected_save_shows_reason(llm_route_db, llm_whitelist):
    """試建客戶端失敗：顯示原因，不寫入。"""
    _, markup, _ = _to_model_screen("haiku")
    save = _button_starting(markup, "✅ 儲存")
    with mock.patch.object(ClaudeCodeLLMClient, "_resolve_cli_path", side_effect=LLMServiceError("找不到 claude")):
        text, _, _ = _click(save)

    assert "無法儲存" in text and "找不到 claude" in text
    assert _stored_rows(llm_route_db) == []


def test_saved_but_screen_update_failed_says_saved(llm_route_db, llm_whitelist):
    """寫入已提交但更新選單訊息失敗：明說「已儲存」，不報成操作失敗。"""
    _, markup, _ = _to_model_screen("haiku")
    query = _callback_query(_button_starting(markup, "✅ 儲存"))
    query.message.edit_text.side_effect = TelegramBadRequest(
        method=mock.MagicMock(), message="Bad Request: message to edit not found"
    )
    asyncio.run(models_menu.handle_menu(query))

    alert = query.answer.await_args.args[0]
    assert alert.startswith("✅ 已儲存，但選單無法更新")
    assert len(_stored_rows(llm_route_db)) == 1


def test_stale_button_after_whitelist_change(llm_route_db, llm_whitelist):
    """白名單改了（例如重新部署）：舊按鈕提示過期，不做任何事。"""
    _, markup = _open_menu()
    old_button = _button_starting(markup, f"{KEY}：")
    with mock.patch.object(settings, "LLM_CLAUDE_CODE_MODELS", ("opus@low",)):
        text, _, query = _click(old_button)

    assert text is None
    query.answer.assert_awaited_once_with(STALE, show_alert=True)


def test_db_failure_while_saving_says_outcome_unknown(llm_route_db, llm_whitelist):
    """儲存時資料庫寫入失敗：提示「結果未知、請重新查看」，不說成「沒存進去」或籠統的操作失敗。"""
    _, markup, _ = _to_model_screen("haiku")
    save = _button_starting(markup, "✅ 儲存")
    error = OperationalError("INSERT ...", {}, Exception("database is locked"))
    with mock.patch.object(capability_route_service, "_session_factory", side_effect=error):
        _, _, query = _click(save)

    alert = query.answer.await_args.args[0]
    assert "結果未知" in alert and "重新查看" in alert


def test_db_down_menu_is_read_only(llm_route_db, llm_whitelist):
    """讀不到資料庫：/setconfig 仍列出清單並附警示，但任何按鈕都只提示讀不到 DB、不做操作。"""
    _, markup = _open_menu()
    button = _button_starting(markup, f"{KEY}：")
    with _db_down():
        routing.invalidate_cache()  # 快取中的設定在故障期間仍有效；清掉才會真的去讀 DB
        text, _ = _open_menu()
        _, _, query = _click(button)

    assert "讀不到資料庫" in text and "選單暫停使用" in text
    alert = query.answer.await_args.args[0]
    assert "讀不到資料庫" in alert
    query.message.edit_text.assert_not_awaited()


@pytest.mark.parametrize("admin_id", [None, ADMIN_ID], ids=["no-admin-configured", "other-user"])
def test_non_admin_is_blocked(llm_route_db, llm_whitelist, admin_id):
    """未設定管理員、或不是管理員：指令與按鈕（含關閉鈕）都被擋下。"""
    with mock.patch.object(settings, "TG_ADMIN_CHAT_ID", admin_id):
        message = _message(user_id=7)
        asyncio.run(models_menu.command_setconfig_handler(message))
        query = _callback_query("lr:l:000000:0", user_id=7)
        asyncio.run(models_menu.handle_menu(query))
        close = _callback_query(CLOSE_DATA, user_id=7)
        asyncio.run(models_menu.handle_close(close))

    expected = "系統未設定管理員" if admin_id is None else "只有管理員"
    assert expected in message.answer.await_args.args[0]
    assert expected in query.answer.await_args.args[0]
    assert expected in close.answer.await_args.args[0]
    query.message.edit_text.assert_not_awaited()
    close.message.edit_text.assert_not_awaited()


def test_message_without_user_is_blocked(llm_route_db, llm_whitelist):
    """訊息沒有發送者（例如頻道訊息）：擋下。"""
    message = _message()
    message.from_user = None
    asyncio.run(models_menu.command_setconfig_handler(message))
    assert "只有管理員" in message.answer.await_args.args[0]


def test_command_error_is_reported(llm_route_db, llm_whitelist, caplog):
    """開啟 /setconfig 時發生非預期錯誤：回覆錯誤訊息並記錄 traceback（不是一片沉默）。"""
    message = _message()
    with mock.patch.object(models_menu.capability_route_service, "views_from", side_effect=RuntimeError("boom")):
        asyncio.run(models_menu.command_setconfig_handler(message))

    assert "無法開啟工具設定" in message.answer.await_args.args[0]
    assert "/setconfig 開啟失敗" in caplog.text


def test_command_error_reply_is_valid_html(llm_route_db, llm_whitelist):
    """錯誤訊息很長且含特殊字元：先截斷再跳脫，回覆裡不會留下截一半的 HTML 實體。"""
    message = _message()
    with mock.patch.object(models_menu.capability_route_service, "views_from", side_effect=RuntimeError("'" * 400)):
        asyncio.run(models_menu.command_setconfig_handler(message))

    reply = message.answer.await_args.args[0]
    assert "&" not in re.sub(r"&(?:#x?[0-9a-fA-F]+|[a-z]+);", "", reply)


def test_unexpected_error_is_reported_with_context(llm_route_db, llm_whitelist, caplog):
    """服務層拋出非預期例外：彈出錯誤訊息，log 帶 user id 與用途鍵。"""
    _, markup = _open_menu()
    button = _button_starting(markup, f"{KEY}：")
    with mock.patch.object(models_menu.capability_route_service, "describe", side_effect=RuntimeError("boom")):
        _, _, query = _click(button)

    message = query.answer.await_args.args[0]
    assert "操作失敗" in message and "boom" in message and "詳見後端 log" in message
    assert f"user={ADMIN_ID}" in caplog.text and f"用途={KEY}" in caplog.text


def test_unchanged_message_is_not_an_error(llm_route_db, llm_whitelist):
    """重按同一個按鈕（內容沒變）時 Telegram 回 400，視為成功。"""
    _, markup = _open_menu()
    query = _callback_query(_button_starting(markup, f"{KEY}："))
    query.message.edit_text.side_effect = TelegramBadRequest(
        method=mock.MagicMock(), message="Bad Request: message is not modified"
    )
    asyncio.run(models_menu.handle_menu(query))

    query.answer.assert_awaited_once_with()


def test_legacy_setconfig_buttons_say_expired():
    """舊 /setconfig 留在聊天裡的按鈕：回覆已過期，不讓按鈕一直轉圈。"""
    query = _callback_query("setconfig_key:AUDIO_MODEL_NAME")
    asyncio.run(models_menu.handle_legacy_setconfig(query))
    query.answer.assert_awaited_once_with(STALE, show_alert=True)


def test_close_button_goes_to_close_handler_only():
    """關閉鈕 lr:x 只被 handle_close 接住；handle_menu 的過濾條件排除它（不依賴註冊順序）。"""
    def matching(data: str) -> set[str]:
        event = CallbackQuery(
            id="1", from_user=User(id=ADMIN_ID, is_bot=False, first_name="a"), chat_instance="c", data=data
        )

        async def check() -> set[str]:
            names = set()
            for handler in models_menu.router.callback_query.handlers:
                passed, _ = await handler.check(event)
                if passed:
                    names.add(handler.callback.__name__)
            return names

        return asyncio.run(check())

    assert matching(CLOSE_DATA) == {"handle_close"}
    assert matching(f"lr:l:{_ctx().version}:0") == {"handle_menu"}


@pytest.mark.parametrize("target", ["restore", "save"])
def test_db_down_blocks_writes_from_old_buttons(llm_route_db, llm_whitelist, target):
    """讀不到資料庫時，舊的「恢復預設」「儲存」按鈕一律只提示、不寫入也不刪除。

    這兩個動作在服務層不先讀 DB（已宣告鍵的刪除、寫入），只靠選單的唯讀防線擋下：DB 間歇故障
    時，版本相同的舊按鈕不能真的改到設定。"""
    asyncio.run(capability_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))
    if target == "restore":
        _, markup = _to_capability(f"{KEY}：")
        button = _button_starting(markup, "↩️ 恢復預設")
    else:
        _, markup, _ = _to_model_screen("haiku")
        button = _button_starting(markup, "✅ 儲存")

    with _db_down():
        routing.invalidate_cache()
        text, _, query = _click(button)

    assert text is None
    assert "讀不到資料庫" in query.answer.await_args.args[0]
    [row] = _stored_rows(llm_route_db)
    assert (row.provider, row.model) == ("yinli", "m1")


def test_detail_shows_skipped_layer(llm_route_db, llm_whitelist):
    """DB 中已不在白名單的設定：清單標 ⚠️、能力詳情列出被略過的原因。"""
    _insert(llm_route_db, route_key=KEY, provider="yinli", model="retired-model")
    text, _ = _to_capability(f"⚠️ {KEY}：")

    assert "已略過" in text and "retired-model" in text


def test_missing_package_does_not_lock_the_menu(llm_route_db, llm_whitelist):
    """生效端點需要的套件沒裝：/setconfig 照常開啟，該列標 ⚠️、詳情說明原因，其他按鈕照常可用
    （管理員才能把設定改到能用的端點）。"""
    from app.infrastructure.llm import factory

    _insert(llm_route_db, route_key=KEY, provider="claude-code", model="opus", effort="low")
    real = factory.resolve_client_class

    def resolve(provider=None):
        if provider == "claude-code":
            raise ModuleNotFoundError("No module named 'jsonschema'", name="jsonschema")
        return real(provider)

    with mock.patch.object(factory, "resolve_client_class", side_effect=resolve):
        _, markup = _open_menu()
        _, markup, _ = _click(_button_starting(markup, f"⚠️ {KEY}："))
        text, markup, query = _click(_button_starting(markup, "文字 LLM"))

    assert "套件未安裝（jsonschema）" in text
    query.answer.assert_awaited_once_with()
    assert any(label.startswith("↩️ 恢復預設") for label in _buttons(markup))


def _restore_default_screen(patch) -> str:
    """設好本用途後，從 /setconfig 點到「恢復預設」；按下時套用 ``patch``，回傳結果畫面文字。"""
    asyncio.run(capability_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))
    _, markup = _to_capability(f"{KEY}：")
    button = _button_starting(markup, "↩️ 恢復預設")
    with patch:
        text, _, _ = _click(button)
    return text


def test_restore_default_warns_when_reading_back_fails(llm_route_db, llm_whitelist):
    """恢復預設後讀回失敗：結果畫面明說顯示的是 .env 預設、無法確認退回後的設定。

    選單本身用快取中的清單（刪除前已載入），所以只有刪除後的讀回會碰到故障。"""
    text = _restore_default_screen(_db_down())

    assert "已恢復預設" in text and "讀不到資料庫，無法確認退回後的設定" in text
    assert _stored_rows(llm_route_db) == []


def test_restore_default_shows_problem_of_the_next_layer(llm_route_db, llm_whitelist):
    """恢復預設後退回的那一層無法使用（.env 端點設定有誤被跳過）：結果畫面列出原因，不讓管理員
    以為已恢復成可用的預設。"""
    from app.core.config import LLMEndpointSettings

    endpoints = {
        **settings.LLM_ENDPOINTS,
        "relay": LLMEndpointSettings(base_url="https://relay.example/v1", api_key=None, models=("x",)),
    }
    with mock.patch.object(settings, "LLM_ENDPOINTS", endpoints), \
         mock.patch.object(settings, "LLM_PROVIDER", "relay"):
        text = _restore_default_screen(nullcontext())

    assert "已恢復預設" in text
    assert html.escape("LLM_PROVIDER='relay'") in text and "API_KEY 未設定" in text


def test_restore_default_says_when_state_is_unavailable(llm_route_db, llm_whitelist):
    """恢復預設已完成、但組不出之後的狀態：結果畫面明說無法顯示，而不是只有標題（與孤兒鍵
    刪除後沒有狀態區分；服務層如何產生這個結果見 test_capability_route_service）。"""
    asyncio.run(capability_route_service.set_route(KEY, "yinli", "m1", None, updated_by="api"))
    _, markup = _to_capability(f"{KEY}：")
    button = _button_starting(markup, "↩️ 恢復預設")
    unavailable = capability_route_service.DeleteResult(True, None, view_unavailable=True)
    with mock.patch.object(capability_route_service, "delete_route", mock.AsyncMock(return_value=unavailable)):
        text, _, _ = _click(button)

    assert "已恢復預設" in text and "無法顯示目前生效的設定" in text


def test_admin_close_button_closes(llm_route_db, llm_whitelist):
    """管理員按關閉：訊息改成已關閉。"""
    query = _callback_query(CLOSE_DATA)
    asyncio.run(models_menu.handle_close(query))

    query.message.edit_text.assert_awaited_once_with("已關閉工具設定。")


def test_models_router_runs_before_catch_all_text_router():
    """/setconfig 的 router 掛在 dispatcher 上，且排在接住所有文字的 messages router 之前。

    aiogram 的 router 只能掛一次，測試後解除掛載，避免影響之後（例如 lifespan）的 setup。"""
    from app.bot.dispatcher import setup_dispatcher
    from app.bot.handlers import messages

    dispatcher = setup_dispatcher()
    try:
        routers = list(dispatcher.sub_routers)
        assert models_menu.router in routers
        assert routers.index(models_menu.router) < routers.index(messages.router)
    finally:
        for sub_router in dispatcher.sub_routers:
            sub_router._parent_router = None


def test_saving_the_global_key_rebuilds_the_global_client(llm_route_db, llm_whitelist):
    """在選單儲存全域 * 後，重建 app.state.llm_client（與 API 相同，診斷端點跟著全域設定）。"""
    from types import SimpleNamespace

    app = SimpleNamespace(state=SimpleNamespace(llm_client=None))
    _, markup = _to_capability("*：")
    _, markup, _ = _click(_button_starting(markup, "⚙️ 設定"))
    _, markup, _ = _click(_button_starting(markup, "yinli"))
    _, markup, _ = _click(_button_starting(markup, "m2"))
    query = _callback_query(_button_starting(markup, "✅ 儲存"))
    asyncio.run(models_menu.handle_menu(query, app=app))

    query.answer.assert_awaited_once_with()
    assert app.state.llm_client.model_label == "(yinli)m2"


def test_long_error_alert_fits_telegram_limit(llm_route_db, llm_whitelist):
    """非預期錯誤的訊息很長：彈出提示截在 Telegram 的 200 字元上限內（超過會被拒收、管理員什麼都看不到）。"""
    _, markup = _open_menu()
    button = _button_starting(markup, f"{KEY}：")
    with mock.patch.object(models_menu.capability_route_service, "describe", side_effect=RuntimeError("x" * 500)):
        _, _, query = _click(button)

    assert len(query.answer.await_args.args[0]) <= 200


def test_close_failure_is_logged_at_warning(llm_route_db, llm_whitelist, caplog):
    """關閉時訊息已無法編輯（例如超過 48 小時）：記 WARNING（預設等級看得到），不是 DEBUG。"""
    import logging

    query = _callback_query(CLOSE_DATA)
    query.message.edit_text.side_effect = TelegramBadRequest(
        method=mock.MagicMock(), message="Bad Request: message can't be edited"
    )
    with caplog.at_level(logging.WARNING, logger=models_menu.__name__):
        asyncio.run(models_menu.handle_close(query))

    assert "關閉時無法更新訊息" in caplog.text


def test_saved_but_alert_also_fails_is_not_reported_as_failure(llm_route_db, llm_whitelist, caplog):
    """已儲存、畫面更新失敗、連提示也送不出去（回呼查詢逾時）：不往外拋，也不再記一次「操作失敗」。"""
    _, markup, _ = _to_model_screen("haiku")
    query = _callback_query(_button_starting(markup, "✅ 儲存"))
    query.message.edit_text.side_effect = TelegramBadRequest(
        method=mock.MagicMock(), message="Bad Request: message to edit not found"
    )
    query.answer.side_effect = TelegramBadRequest(method=mock.MagicMock(), message="Bad Request: query is too old")
    asyncio.run(models_menu.handle_menu(query))

    assert "連提示都送不出去" in caplog.text
    assert "選單操作失敗" not in caplog.text
    assert len(_stored_rows(llm_route_db)) == 1


def test_restoring_the_global_key_rebuilds_the_global_client(llm_route_db, llm_whitelist):
    """在選單恢復全域 * 的預設後，重建 app.state.llm_client，改回 .env 那一層。"""
    from types import SimpleNamespace

    asyncio.run(capability_route_service.set_route("*", "yinli", "m2", None, updated_by="api"))
    app = SimpleNamespace(state=SimpleNamespace(llm_client=None))
    _, markup = _to_capability("*：")
    query = _callback_query(_button_starting(markup, "↩️ 恢復預設"))
    asyncio.run(models_menu.handle_menu(query, app=app))

    assert app.state.llm_client.model_label == "gemini-3.5-flash"
    assert _stored_rows(llm_route_db) == []


def test_global_change_without_app_is_logged(llm_route_db, llm_whitelist, caplog):
    """改了全域 * 卻沒有注入 app（dispatcher 組法變了）：無法重建，但要記 WARNING，不是無聲略過。"""
    _, markup = _to_capability("*：")
    _, markup, _ = _click(_button_starting(markup, "⚙️ 設定"))
    _, markup, _ = _click(_button_starting(markup, "yinli"))
    _, markup, _ = _click(_button_starting(markup, "m2"))
    _click(_button_starting(markup, "✅ 儲存"))

    assert "沒有注入 app" in caplog.text


def test_saved_but_alert_fails_on_network_error_is_not_reported_as_failure(llm_route_db, llm_whitelist, caplog):
    """已儲存、畫面更新失敗、連提示也因網路錯誤送不出去：同樣不往外拋、不記「操作失敗」。"""
    from aiogram.exceptions import TelegramNetworkError

    _, markup, _ = _to_model_screen("haiku")
    query = _callback_query(_button_starting(markup, "✅ 儲存"))
    query.message.edit_text.side_effect = TelegramBadRequest(
        method=mock.MagicMock(), message="Bad Request: message to edit not found"
    )
    query.answer.side_effect = TelegramNetworkError(method=mock.MagicMock(), message="connection reset")
    asyncio.run(models_menu.handle_menu(query))

    assert "連提示都送不出去" in caplog.text
    assert "選單操作失敗" not in caplog.text
    assert len(_stored_rows(llm_route_db)) == 1
