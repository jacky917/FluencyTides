"""
TG ``/setconfig`` 選單：管理每個用途各能力用的工具（能力路由計畫 capability_routing_FEAT_2026-10-19.md §8.5；
前身為模型路由計畫 llm_model_routing_FEAT_2026-10-15.md D7 的 ``/models``）。

Telegram ``/setconfig`` menu for managing the tools of each route key and capability.

流程::

    /setconfig → 用途清單（可翻頁，第一列是全域 *）
                 └─ 選一個用途 → 選能力（只列該鍵宣告的）
                                 └─ [設定] → 選 provider → 選模型 →（有 effort 時）選 effort → 確認 → 儲存
                                 └ [恢復預設] → 刪除該筆設定，顯示退回後生效的設定

每一層只列白名單允許的選項：``llm`` / ``audio_llm`` 為 ``.env`` 的端點白名單、``stt`` 為
``STT_MODELS``、``audio_eval`` 為固定的策略清單（用途鍵可再限縮）。策略不選模型，選完直接到確認；
effort 只有一個時自動選上，模型不使用 effort 時跳過這一層。寫入一律經
:mod:`app.services.capability_route_service`（白名單＋試建），與 API 相同；設定存在資料庫，
重啟後仍在。

**不使用狀態機**：每個按鈕的 callback data 自帶目前選到哪一步，格式為
``lr:<動作>:<版本>:<序號…>``，以序號代替名稱以符合 Telegram 的 64 位元組上限。
「版本」是用途清單（含各鍵的能力與策略限縮）與各能力白名單的雜湊：清單變了（例如重新部署改了
``.env``、刪了孤兒設定），舊按鈕的序號可能已指向別的項目，這時一律提示「選單已過期」而不是誤操作。
例外：關閉鈕 ``lr:x`` 不帶版本，任何時候都能按。沒有狀態殘留，也不會和其他流程搶
使用者輸入。只有 ``TG_ADMIN_CHAT_ID`` 可以使用。

讀不到資料庫時，``/setconfig`` 仍列出清單（皆為 ``.env`` 預設並附警示），但不接受任何
操作（關閉鈕除外）——那時無法判斷各用途存了什麼，不能讓管理員以為「本來就沒有設定」。

English summary:
    Stateless inline-keyboard menu: every callback carries its position
    (indices plus a version hash of the key list and whitelists), so stale
    buttons are detected instead of misapplied. Writes go through the same
    service as the HTTP API. Admin only; read-only while the DB is down.
"""

import hashlib
import html
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from app.core.config import settings
from app.core.exceptions import InvalidRouteError, RouteNotFoundError, RouteStoreUnavailableError
from app.infrastructure.llm import routing
from app.infrastructure.llm.capabilities import (
    CAPABILITY_AUDIO_EVAL,
    CAPABILITY_AUDIO_LLM,
    CAPABILITY_LLM,
    CAPABILITY_ORDER,
    CAPABILITY_STT,
    CAPABILITY_TITLES,
    STRATEGY_CAPABILITIES,
    ProviderOption,
    list_providers,
)
from app.infrastructure.llm.endpoints import ModelOption
from app.infrastructure.llm.routing import GLOBAL_ROUTE_KEY
from app.services import capability_route_service
from app.services.capability_route_service import KeyView, RouteView

logger = logging.getLogger(__name__)

router = Router(name="models_menu_router")

COMMAND = "setconfig"
PREFIX = "lr"
CLOSE_DATA = f"{PREFIX}:x"
PAGE_SIZE = 10
# callback data 中「不選模型」與「不使用 effort」的記號
NO_MODEL = "-"
NO_EFFORT = "-"
# Telegram 的 callback data 上限（位元組）
CALLBACK_DATA_LIMIT = 64
# 按鈕文字截斷長度：Telegram 沒有硬上限，太長在手機上會換很多行
BUTTON_TEXT_LIMIT = 60
# answerCallbackQuery 的提示文字上限為 200 字元，留一點餘裕
ALERT_TEXT_LIMIT = 190

_SOURCE_LABELS = {"route": "用途", "parent": "流程", "global": "全域", "env": ".env"}
_STALE_ALERT = f"選單已過期，請重新輸入 /{COMMAND}。"
# 讀不到 DB 時整個選單都拒絕操作（含翻頁、看詳情），不只是修改
_DB_ALERT = f"⚠️ 讀不到資料庫，選單暫停使用，請稍後重新輸入 /{COMMAND}。"

# 某能力沒有可選的 provider 時，說明白名單在哪裡設定
_EMPTY_PROVIDERS = {
    CAPABILITY_LLM: ".env 的白名單都是空的（LLM_ENDPOINTS__&lt;ID&gt;__MODELS、LLM_CLAUDE_CODE_MODELS…）。",
    CAPABILITY_AUDIO_LLM: "沒有能聽音檔的端點（AUDIO_API_KEY 未設定，或 LLM_ENDPOINTS__&lt;ID&gt;__AUDIO_MODELS 都是空的）。",
    CAPABILITY_STT: "STT_SERVER_URL 未設定，或 STT_MODEL_NAME / STT_MODELS 都是空的。",
    CAPABILITY_AUDIO_EVAL: "這個用途沒有允許任何評分策略。",
}


class Action(StrEnum):
    """callback data 的動作碼。Action codes carried in callback data."""

    LIST = "l"        # 清單（頁碼）
    DETAIL = "k"      # 用途詳情（用途）
    CAPABILITY = "a"  # 能力詳情（用途, 能力）
    PROVIDERS = "p"   # 選 provider（用途, 能力）
    MODELS = "m"      # 選模型（用途, 能力, provider）
    EFFORTS = "f"     # 選 effort（用途, 能力, provider, 模型）
    CONFIRM = "c"     # 確認（用途, 能力, provider, 模型記號, effort 記號）
    SAVE = "s"        # 儲存（用途, 能力, provider, 模型記號, effort 記號）
    DELETE = "d"      # 恢復預設（用途, 能力）


# 各動作的參數個數。_TOKEN_ARGS 中的動作，最後兩個參數是模型記號（序號或 NO_MODEL）與 effort 記號
# （序號或 NO_EFFORT），由 resolve_selection 解析、不經 _index；其餘參數都是整數序號
_ARITY: dict[Action, int] = {
    Action.LIST: 1, Action.DETAIL: 1, Action.CAPABILITY: 2, Action.PROVIDERS: 2, Action.MODELS: 3,
    Action.EFFORTS: 4, Action.CONFIRM: 5, Action.SAVE: 5, Action.DELETE: 2,
}
_TOKEN_ARGS = frozenset({Action.CONFIRM, Action.SAVE})


class StaleMenuError(Exception):
    """按鈕所屬的選單已過期（清單變了、序號無效、格式不符）。The button is outdated or malformed."""


class _AlreadyAnswered(Exception):
    """已自行回覆了這次回呼查詢，外層不必再 answer。The callback query was already answered."""


@dataclass(frozen=True)
class MenuKey:
    """選單中的一個用途鍵。One route key in the menu.

    Attributes:
        key: 用途鍵。The route key.
        capabilities: 列出的能力（宣告的能力，加上 DB 中存了值、但沒有宣告的）。Listed capabilities.
        declared: 這個鍵宣告的能力（只有這些可以設定）。Capabilities that may be set.
        strategies: 允許的評分策略；None 為全部。Allowed strategies, or None.
    """

    key: str
    capabilities: tuple[str, ...]
    declared: frozenset[str] = frozenset()
    strategies: frozenset[str] | None = None


def _fingerprint(keys: tuple[MenuKey, ...], providers: Mapping[str, tuple[ProviderOption, ...]]) -> str:
    """用途清單＋各能力白名單的雜湊（6 碼）。A short hash of the key list and whitelists."""
    text = repr((
        [(k.key, k.capabilities, sorted(k.declared), sorted(k.strategies or ())) for k in keys],
        [
            (capability, [(p.id, [(o.name, o.efforts) for o in p.models]) for p in providers.get(capability, ())])
            for capability in CAPABILITY_ORDER
        ],
    ))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]


@dataclass(frozen=True)
class MenuContext:
    """一次操作所需的清單快照。The lists one menu interaction works on.

    Attributes:
        keys: 依顯示順序的用途鍵，第一個一定是全域 ``*``。Route keys, ``*`` first.
        providers: 能力 → 可選的 provider（白名單非空；``audio_eval`` 為全部策略）。
            Selectable providers per capability.
        db_ok: 這份快照是否讀到了資料庫。Whether the DB was readable.
        version: 由 ``keys`` 與 ``providers`` 算出的雜湊，寫進每個按鈕（不可指定）。
            Derived from ``keys`` and ``providers``; not settable.
    """

    keys: tuple[MenuKey, ...]
    providers: Mapping[str, tuple[ProviderOption, ...]] = field(compare=False)
    db_ok: bool = True
    version: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.keys or self.keys[0].key != GLOBAL_ROUTE_KEY:
            raise ValueError("用途清單的第一項必須是全域 *")
        object.__setattr__(self, "version", _fingerprint(self.keys, self.providers))

    def key_at(self, index: int) -> MenuKey:
        """序號對應的用途鍵。The route key at ``index``.

        Raises:
            StaleMenuError: 序號越界（清單已變）。
        """
        if not 0 <= index < len(self.keys):
            raise StaleMenuError
        return self.keys[index]

    def capability_at(self, key_index: int, capability_index: int) -> str:
        """序號對應的能力。The capability at the given indices.

        Raises:
            StaleMenuError: 序號越界。
        """
        capabilities = self.key_at(key_index).capabilities
        if not 0 <= capability_index < len(capabilities):
            raise StaleMenuError
        return capabilities[capability_index]

    def providers_for(self, key_index: int, capability_index: int) -> tuple[ProviderOption, ...]:
        """這個鍵在這個能力上可選的 provider（評分策略依鍵的限縮過濾）。The selectable providers.

        Raises:
            StaleMenuError: 序號越界。
        """
        menu_key = self.key_at(key_index)
        capability = self.capability_at(key_index, capability_index)
        options = self.providers.get(capability, ())
        if capability == CAPABILITY_AUDIO_EVAL and menu_key.strategies is not None:
            options = tuple(option for option in options if option.id in menu_key.strategies)
        return options

    def provider_at(self, key_index: int, capability_index: int, provider_index: int) -> ProviderOption:
        """序號對應的 provider。The provider at the given indices.

        Raises:
            StaleMenuError: 序號越界。
        """
        options = self.providers_for(key_index, capability_index)
        if not 0 <= provider_index < len(options):
            raise StaleMenuError
        return options[provider_index]

    def model_at(self, key_index: int, capability_index: int, provider_index: int, model_index: int) -> ModelOption:
        """序號對應的白名單模型。The whitelisted model at the given indices.

        Raises:
            StaleMenuError: 序號越界。
        """
        models = self.provider_at(key_index, capability_index, provider_index).models
        if not 0 <= model_index < len(models):
            raise StaleMenuError
        return models[model_index]


def build_context(
    keys: list[MenuKey], providers: Mapping[str, tuple[ProviderOption, ...]] | None = None, *, db_ok: bool = True
) -> MenuContext:
    """組出選單快照（版本由內容算出）；``providers`` 省略時讀目前的白名單。Build a menu context."""
    if providers is None:
        providers = {capability: tuple(list_providers(capability)) for capability in CAPABILITY_ORDER}
    return MenuContext(tuple(keys), dict(providers), db_ok)


def menu_key(view: KeyView) -> MenuKey:
    """服務層的用途鍵狀態轉成選單項目。A key view as a menu key."""
    return MenuKey(
        view.route_key,
        view.capabilities,
        view.entry.capabilities,
        routing.allowed_strategies(view.route_key),
    )


async def load_menu() -> tuple[MenuContext, list[KeyView]]:
    """一次讀取，同時產生選單快照與清單內容（兩者來自同一份 DB 快照）。

    Load the menu context and the list rows from a single read, so the
    version and the rows can never disagree.
    """
    stored, read_ok = await routing.stored_choices()
    views = capability_route_service.views_from(stored, read_ok)
    return build_context([menu_key(view) for view in views], db_ok=read_ok), views


async def load_context() -> MenuContext:
    """讀取目前的用途清單與白名單。Load the current key list and whitelists."""
    ctx, _ = await load_menu()
    return ctx


def callback(ctx: MenuContext, action: Action, *args: int | str) -> str:
    """組 callback data。Compose callback data for a button.

    Raises:
        ValueError: 超過 64 位元組（序號設計下不應發生；測試鎖住上限）。
    """
    data = ":".join([PREFIX, action.value, ctx.version, *(str(arg) for arg in args)])
    if len(data.encode("utf-8")) > CALLBACK_DATA_LIMIT:
        raise ValueError(f"callback data 超過 {CALLBACK_DATA_LIMIT} 位元組：{data}")
    return data


def parse_callback(data: str, ctx: MenuContext) -> tuple[Action, list[str]]:
    """拆出動作與參數，並核對版本與參數個數。Split action and args, checking version and arity.

    Raises:
        StaleMenuError: 格式不符、版本不同、動作未知或參數個數不對。
    """
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != PREFIX or parts[2] != ctx.version:
        raise StaleMenuError
    try:
        action = Action(parts[1])
    except ValueError as error:
        raise StaleMenuError from error
    args = parts[3:]
    if len(args) != _ARITY[action]:
        raise StaleMenuError
    return action, args


def _index(raw: str) -> int:
    """把序號字串轉成整數。Parse an index argument.

    Raises:
        StaleMenuError: 不是整數。
    """
    try:
        return int(raw)
    except ValueError as error:
        raise StaleMenuError from error


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text[:BUTTON_TEXT_LIMIT], callback_data=data)


def _close_button() -> InlineKeyboardButton:
    return _button("✖️ 關閉", CLOSE_DATA)


def _title(capability: str) -> str:
    """能力的中文名稱。The capability's display name."""
    return CAPABILITY_TITLES.get(capability, capability)


def _describe_effective(view: RouteView) -> str:
    """「(yinli)gemini-3.5-flash（來源：用途）」這樣的一行。One-line effective setting."""
    return f"<code>{html.escape(view.label)}</code>（來源：{_SOURCE_LABELS[view.resolved.source]}）"


def _needs_attention(view: RouteView) -> bool:
    """孤兒設定、有被略過的層，或生效設定目前無法使用。Whether to mark the row with ⚠️."""
    return not view.capability_declared or bool(view.resolved.rejected) or view.problem is not None


def _in_use(view: KeyView) -> list[RouteView]:
    """目前會用到的能力：有評分策略的鍵只算策略本身與策略用到的能力，其他鍵算全部。

    清單的 ⚠️ 只看這些——例如策略是 listen 時，STT 沒設定不影響評分，不必標出來。
    The capabilities currently in use; the list marks only problems among them.
    """
    strategy = view.view(CAPABILITY_AUDIO_EVAL)
    if strategy is None:
        return list(view.views)
    used = {CAPABILITY_AUDIO_EVAL, *STRATEGY_CAPABILITIES.get(strategy.resolved.provider, ())}
    if view.route_key == GLOBAL_ROUTE_KEY:
        used.add(CAPABILITY_LLM)  # 全域的 llm 也是各建卡用途的預設
    return [item for item in view.views if item.capability in used or not item.capability_declared]


def _summary_view(view: KeyView) -> RouteView | None:
    """清單一列要顯示的能力：錄音評分類的鍵顯示評分策略，其他顯示第一個能力。The row's headline."""
    if view.route_key != GLOBAL_ROUTE_KEY and view.view(CAPABILITY_AUDIO_EVAL) is not None:
        return view.view(CAPABILITY_AUDIO_EVAL)
    return view.views[0] if view.views else None


# ----------------------------------------------------------------------
# 各畫面（Screens）：回傳 (文字, 鍵盤)
# ----------------------------------------------------------------------


def render_list(ctx: MenuContext, views: list[KeyView], page: int) -> tuple[str, InlineKeyboardMarkup]:
    """用途清單（每頁 :data:`PAGE_SIZE` 個）；``⚠️`` 標出孤兒設定、有被略過的層，以及生效設定
    目前無法使用的用途。頁碼夾在有效範圍內。分支鍵縮排在流程鍵之下。

    The paged route list; ``⚠️`` marks orphan rows, keys with a skipped
    layer, and keys whose effective setting is unusable. The page number is
    clamped into range; branch keys are indented under their flow key.
    """
    pages = max(1, -(-len(views) // PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    rows: list[list[InlineKeyboardButton]] = []
    listed = {view.route_key for view in views}
    for index in range(page * PAGE_SIZE, min(len(views), (page + 1) * PAGE_SIZE)):
        view = views[index]
        marker = "⚠️ " if not view.declared or any(_needs_attention(item) for item in _in_use(view)) else ""
        indent = "　└ " if routing.parent_key(view.route_key) in listed else ""
        headline = _summary_view(view)
        summary = (
            f"{headline.label}［{_SOURCE_LABELS[headline.resolved.source]}］" if headline is not None else "（無）"
        )
        more = f" 等 {len(view.views)} 項" if len(view.views) > 1 else ""
        text = f"{indent}{marker}{view.route_key}：{summary}{more}"
        rows.append([_button(text, callback(ctx, Action.DETAIL, index))])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(_button("◀️ 上一頁", callback(ctx, Action.LIST, page - 1)))
    if page < pages - 1:
        nav.append(_button("下一頁 ▶️", callback(ctx, Action.LIST, page + 1)))
    if nav:
        rows.append(nav)
    rows.append([_close_button()])

    text = (
        "🛠️ <b>工具設定</b>\n"
        "選一個用途查看或修改它用的工具（文字 LLM、錄音評分策略、聽音檔的模型、語音轉文字）。"
        "設定存在資料庫，重啟後仍有效。\n"
        "來源：<b>用途</b>＝該用途自己的設定、<b>流程</b>＝分支沿用流程鍵的設定、<b>全域</b>＝* 的設定、"
        "<b>.env</b>＝部署預設。"
    )
    if not ctx.db_ok:
        text += "\n\n⚠️ 讀不到資料庫：以下為 .env 預設，實際設定未知，選單暫停使用。"
    if pages > 1:
        text += f"\n\n第 {page + 1} / {pages} 頁"
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def render_detail(ctx: MenuContext, key_index: int, view: KeyView) -> tuple[str, InlineKeyboardMarkup]:
    """單一用途：各能力目前生效的設定，選一個能力修改。One route's capabilities."""
    menu_key = ctx.key_at(key_index)
    if view.capabilities != menu_key.capabilities:
        raise StaleMenuError
    lines = [f"<b>{html.escape(view.route_key)}</b>"]
    if view.description:
        lines.append(html.escape(view.description))
    if not view.declared:
        lines.append("⚠️ 程式中已沒有這個用途（孤兒設定），可恢復預設以刪除。")
    lines.append("")
    for item in view.views:
        marker = "⚠️ " if _needs_attention(item) else ""
        lines.append(f"{marker}<b>{_title(item.capability)}</b>：{_describe_effective(item)}")

    rows = [
        [_button(f"{_title(capability)}（{capability}）", callback(ctx, Action.CAPABILITY, key_index, index))]
        for index, capability in enumerate(menu_key.capabilities)
    ]
    rows.append([_button("⬅️ 返回清單", callback(ctx, Action.LIST, key_index // PAGE_SIZE)), _close_button()])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def render_capability(
    ctx: MenuContext, key_index: int, capability_index: int, view: RouteView
) -> tuple[str, InlineKeyboardMarkup]:
    """單一用途在一種能力上的詳情。One route's details for one capability."""
    lines = [f"<b>{html.escape(view.route_key)}</b> → <b>{_title(view.capability)}</b>（{view.capability}）"]
    if not view.capability_declared:
        lines.append("⚠️ 程式中這個用途已不用這個能力（孤兒設定），可恢復預設以刪除。")
    lines.append("")
    lines.append(f"目前生效：{_describe_effective(view)}")
    if view.problem:
        lines.append(f"⚠️ {html.escape(view.problem)}")
    if view.stored is not None:
        stored = view.stored
        model = f" / {stored.model}" if stored.model else ""
        effort = f" @{stored.effort}" if stored.effort else ""
        lines.append(f"本用途的設定：<code>{html.escape(f'{stored.endpoint}{model}{effort}')}</code>")
    for layer in view.resolved.rejected:
        lines.append(f"⚠️ 已略過 <code>{html.escape(layer.route_key)}</code>：{html.escape(layer.reason)}")

    rows: list[list[InlineKeyboardButton]] = []
    if view.capability_declared:
        rows.append([_button("⚙️ 設定", callback(ctx, Action.PROVIDERS, key_index, capability_index))])
    if view.stored is not None:
        rows.append([_button("↩️ 恢復預設", callback(ctx, Action.DELETE, key_index, capability_index))])
    rows.append([_button("⬅️ 返回", callback(ctx, Action.DETAIL, key_index)), _close_button()])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _provider_text(capability: str, option: ProviderOption) -> str:
    """provider 按鈕的文字。The provider button's text."""
    if capability == CAPABILITY_AUDIO_EVAL:
        return f"{option.id} — {option.title}" if option.title else option.id
    if option.type == option.id:
        return option.id
    return f"{option.id}（{option.type}）"


def _next_after_provider(ctx: MenuContext, key_index: int, capability_index: int, provider_index: int) -> str:
    """選完 provider 後的下一步：要選模型就去選模型；不選模型（策略）直接到確認。

    Where picking a provider leads: the model list, or straight to
    confirmation for providers without a model.
    """
    option = ctx.provider_at(key_index, capability_index, provider_index)
    if option.needs_model:
        return callback(ctx, Action.MODELS, key_index, capability_index, provider_index)
    return callback(ctx, Action.CONFIRM, key_index, capability_index, provider_index, NO_MODEL, NO_EFFORT)


def render_providers(ctx: MenuContext, key_index: int, capability_index: int) -> tuple[str, InlineKeyboardMarkup]:
    """選 provider。Pick a provider."""
    key = ctx.key_at(key_index).key
    capability = ctx.capability_at(key_index, capability_index)
    options = ctx.providers_for(key_index, capability_index)
    rows = [
        [_button(_provider_text(capability, option), _next_after_provider(ctx, key_index, capability_index, index))]
        for index, option in enumerate(options)
    ]
    rows.append([_button("⬅️ 返回", callback(ctx, Action.CAPABILITY, key_index, capability_index)), _close_button()])
    head = f"<b>{html.escape(key)}</b> → <b>{_title(capability)}</b>"
    if not options:
        text = f"{head}\n\n⚠️ 沒有可選的項目：{_EMPTY_PROVIDERS.get(capability, '白名單是空的。')}"
    elif capability == CAPABILITY_AUDIO_EVAL:
        text = f"{head}\n選擇評分策略（策略用到的模型在「聽音檔的模型」「語音轉文字」「文字 LLM」設定）："
    else:
        text = f"{head}\n選擇{'端點' if capability in (CAPABILITY_LLM, CAPABILITY_AUDIO_LLM) else '服務'}："
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _next_after_model(
    ctx: MenuContext, key_index: int, capability_index: int, provider_index: int, model_index: int
) -> str:
    """選完模型後的下一步：多個 effort 去選 effort；只有一個時自動帶入序號 0、不使用 effort
    時帶 :data:`NO_EFFORT`，直接到確認。

    Where picking a model leads: the effort list, or straight to confirmation
    with the single effort (index 0) or :data:`NO_EFFORT`.
    """
    option = ctx.model_at(key_index, capability_index, provider_index, model_index)
    if len(option.efforts) > 1:
        return callback(ctx, Action.EFFORTS, key_index, capability_index, provider_index, model_index)
    effort = "0" if option.efforts else NO_EFFORT
    return callback(ctx, Action.CONFIRM, key_index, capability_index, provider_index, model_index, effort)


def render_models(
    ctx: MenuContext, key_index: int, capability_index: int, provider_index: int
) -> tuple[str, InlineKeyboardMarkup]:
    """選模型。Pick a model."""
    key = ctx.key_at(key_index).key
    capability = ctx.capability_at(key_index, capability_index)
    provider = ctx.provider_at(key_index, capability_index, provider_index)
    rows = [
        [_button(option.name, _next_after_model(ctx, key_index, capability_index, provider_index, index))]
        for index, option in enumerate(provider.models)
    ]
    rows.append([_button("⬅️ 返回", callback(ctx, Action.PROVIDERS, key_index, capability_index)), _close_button()])
    text = (
        f"<b>{html.escape(key)}</b> → <b>{_title(capability)}</b> → <code>{html.escape(provider.id)}</code>\n"
        "選擇模型："
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def render_efforts(
    ctx: MenuContext, key_index: int, capability_index: int, provider_index: int, model_index: int
) -> tuple[str, InlineKeyboardMarkup]:
    """選 effort。Pick an effort."""
    key = ctx.key_at(key_index).key
    provider = ctx.provider_at(key_index, capability_index, provider_index)
    option = ctx.model_at(key_index, capability_index, provider_index, model_index)
    rows = [
        [_button(effort, callback(
            ctx, Action.CONFIRM, key_index, capability_index, provider_index, model_index, index,
        ))]
        for index, effort in enumerate(option.efforts)
    ]
    rows.append([
        _button("⬅️ 返回", callback(ctx, Action.MODELS, key_index, capability_index, provider_index)),
        _close_button(),
    ])
    text = (
        f"<b>{html.escape(key)}</b> → <code>{html.escape(provider.id)}</code> / "
        f"<code>{html.escape(option.name)}</code>\n選擇思考深度（effort）："
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def resolve_selection(
    ctx: MenuContext, key_index: int, capability_index: int, provider_index: int, model_token: str, effort_token: str
) -> tuple[ProviderOption, str, str | None]:
    """把序號換成 ``(provider, 模型名, effort)``。Turn indices into the actual choice.

    Raises:
        StaleMenuError: 序號無效，或模型、effort 記號與白名單不符。
    """
    provider = ctx.provider_at(key_index, capability_index, provider_index)
    if model_token == NO_MODEL:
        if provider.needs_model or effort_token != NO_EFFORT:
            raise StaleMenuError
        return provider, "", None
    option = ctx.model_at(key_index, capability_index, provider_index, _index(model_token))
    if effort_token == NO_EFFORT:
        if option.efforts:
            raise StaleMenuError
        return provider, option.name, None
    effort_index = _index(effort_token)
    if not 0 <= effort_index < len(option.efforts):
        raise StaleMenuError
    return provider, option.name, option.efforts[effort_index]


def render_confirm(
    ctx: MenuContext, key_index: int, capability_index: int, provider_index: int, model_token: str, effort_token: str
) -> tuple[str, InlineKeyboardMarkup]:
    """儲存前確認，顯示生效後的標籤。Confirm before saving."""
    key = ctx.key_at(key_index).key
    capability = ctx.capability_at(key_index, capability_index)
    provider, model, effort = resolve_selection(
        ctx, key_index, capability_index, provider_index, model_token, effort_token
    )
    label = capability_route_service.preview_label(provider.id, model, effort, capability=capability)
    if model_token == NO_MODEL:
        back = callback(ctx, Action.PROVIDERS, key_index, capability_index)
    elif len(ctx.model_at(key_index, capability_index, provider_index, _index(model_token)).efforts) > 1:
        back = callback(ctx, Action.EFFORTS, key_index, capability_index, provider_index, _index(model_token))
    else:
        back = callback(ctx, Action.MODELS, key_index, capability_index, provider_index)
    rows = [
        [_button("✅ 儲存", callback(
            ctx, Action.SAVE, key_index, capability_index, provider_index, model_token, effort_token,
        ))],
        [_button("⬅️ 返回", back), _close_button()],
    ]
    check = "只檢查策略名" if capability == CAPABILITY_AUDIO_EVAL else "會先試建一次（檢查 CLI、金鑰、伺服器設定）"
    text = (
        f"<b>{html.escape(key)}</b> 的 <b>{_title(capability)}</b> 改用：\n<code>{html.escape(label)}</code>\n\n"
        f"儲存時{check}，之後立即生效、重啟後仍在。"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def render_result(
    ctx: MenuContext, key_index: int, title: str, view: RouteView | None, *, view_unavailable: bool = False
) -> tuple[str, InlineKeyboardMarkup]:
    """儲存或恢復預設後的結果。The result after a save or delete.

    Args:
        view: 之後的狀態；刪除孤兒設定、或組不出狀態時為 None。The state afterwards.
        view_unavailable: 寫入已完成但組不出之後的狀態（與孤兒設定刪除後的 None 區分）。
            Whether the state could not be built.
    """
    lines = [title]
    if view is not None:
        lines.append(f"{html.escape(view.route_key)} → {_title(view.capability)}：{_describe_effective(view)}")
        if view.resolved.db_unavailable:
            lines.append("⚠️ 讀不到資料庫，無法確認退回後的設定，上面顯示的是 .env 預設。")
        if view.problem:
            lines.append(f"⚠️ {html.escape(view.problem)}")
    elif view_unavailable:
        lines.append(f"⚠️ 無法顯示目前生效的設定（詳見後端 log），請重新輸入 /{COMMAND} 查看。")
    rows = [[_button("⬅️ 返回清單", callback(ctx, Action.LIST, key_index // PAGE_SIZE)), _close_button()]]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def render_error(
    ctx: MenuContext, key_index: int, capability_index: int, message: str
) -> tuple[str, InlineKeyboardMarkup]:
    """寫入被拒時的畫面。Shown when a write is rejected."""
    rows = [[_button("⬅️ 返回", callback(ctx, Action.CAPABILITY, key_index, capability_index)), _close_button()]]
    return f"❌ 無法儲存：{html.escape(message)}", InlineKeyboardMarkup(inline_keyboard=rows)


# ----------------------------------------------------------------------
# Handlers
# ----------------------------------------------------------------------


def admin_denial(user: User | None) -> str | None:
    """不是管理員時的提示；是管理員時為 None。Why the user may not use the menu, or None."""
    if settings.TG_ADMIN_CHAT_ID is None:
        return f"⛔ 系統未設定管理員（TG_ADMIN_CHAT_ID），/{COMMAND} 目前無法使用。"
    if user is None or user.id != settings.TG_ADMIN_CHAT_ID:
        return f"⛔ 只有管理員可以使用 /{COMMAND}。"
    return None


async def _edit(callback_query: CallbackQuery, screen: tuple[str, InlineKeyboardMarkup]) -> None:
    """更新選單訊息；內容沒變時 Telegram 會回 400，視為成功。Update the menu message.

    Raises:
        StaleMenuError: 訊息已刪除或無法存取（handle_menu 已先擋下，這裡是型別防護）。
    """
    text, markup = screen
    message = callback_query.message
    if message is None or isinstance(message, InaccessibleMessage):
        raise StaleMenuError
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise
        logger.debug("/%s 選單內容未變，略過更新。", COMMAND)


@router.message(Command(COMMAND))
async def command_setconfig_handler(message: Message) -> None:
    """``/setconfig``：顯示用途清單。Show the route list."""
    denial = admin_denial(message.from_user)
    if denial is not None:
        await message.answer(denial)
        return
    try:
        ctx, views = await load_menu()
        text, markup = render_list(ctx, views, 0)
    except Exception as error:  # noqa: BLE001 - 指令入口：記錄完整 traceback 並回報給管理員
        logger.exception("/%s 開啟失敗（user=%s）", COMMAND, message.from_user.id if message.from_user else None)
        # 先截斷再跳脫：bot 預設 parse_mode=HTML，截在實體中間會讓這則回覆本身送不出去
        detail = f"{type(error).__name__}: {error}"[:300]
        await message.answer(f"❌ 無法開啟工具設定：{html.escape(detail)}\n詳見後端 log。")
        return
    await message.answer(text, reply_markup=markup)


# handle_menu 的過濾條件排除了 lr:x，兩者互斥，與註冊順序無關
@router.callback_query(F.data == CLOSE_DATA)
async def handle_close(callback_query: CallbackQuery) -> None:
    """關閉選單。Close the menu."""
    denial = admin_denial(callback_query.from_user)
    if denial is not None:
        await callback_query.answer(denial, show_alert=True)
        return
    await callback_query.answer()
    message = callback_query.message
    if message is None or isinstance(message, InaccessibleMessage):
        return
    try:
        await message.edit_text("已關閉工具設定。")
    except TelegramBadRequest as error:
        if "message is not modified" in str(error):
            return
        # 例如訊息超過 48 小時已不能編輯：管理員按了沒反應，至少要在預設等級的 log 裡看得到
        logger.warning("/%s 關閉時無法更新訊息（user=%s）：%s", COMMAND, callback_query.from_user.id, error)


# 舊的暫時性 /setconfig（MODIFY_*，已於能力路由 R2 移除）留在聊天裡的按鈕：回覆已過期，不讓按鈕一直轉圈
_LEGACY_SETCONFIG_PREFIXES = ("setconfig_", "setcfg_val:")


@router.callback_query(F.data.startswith(_LEGACY_SETCONFIG_PREFIXES))
async def handle_legacy_setconfig(callback_query: CallbackQuery) -> None:
    """舊 ``/setconfig`` 選單的按鈕。Buttons left over from the removed ``/setconfig`` menu."""
    await callback_query.answer(_STALE_ALERT, show_alert=True)


@router.callback_query(F.data.startswith(f"{PREFIX}:") & (F.data != CLOSE_DATA))
async def handle_menu(callback_query: CallbackQuery, app: Any = None) -> None:
    """處理選單中的所有按鈕（關閉鈕除外）。Handle every menu button except close.

    Args:
        callback_query: 回呼查詢。The callback query.
        app: dispatcher 注入的 FastAPI app（寫入或刪除全域 ``*`` 的 ``llm`` 後重建診斷用的全域客戶端；
            沒有時略過重建）。The FastAPI app injected by the dispatcher.
    """
    denial = admin_denial(callback_query.from_user)
    if denial is not None:
        await callback_query.answer(denial, show_alert=True)
        return
    if (
        callback_query.message is None
        or isinstance(callback_query.message, InaccessibleMessage)
        or callback_query.data is None
    ):
        await callback_query.answer(_STALE_ALERT, show_alert=True)
        return

    ctx: MenuContext | None = None
    try:
        ctx, views = await load_menu()
        if not ctx.db_ok:
            logger.warning(
                "/%s：讀不到資料庫，拒絕操作（user=%s, data=%s）",
                COMMAND, callback_query.from_user.id, callback_query.data,
            )
            await callback_query.answer(_DB_ALERT, show_alert=True)
            return
        action, args = parse_callback(callback_query.data, ctx)
        await _dispatch(callback_query, ctx, views, action, args, app)
    except _AlreadyAnswered:
        return
    except (StaleMenuError, RouteNotFoundError) as error:
        # NotFound 也算過期：孤兒鍵在選單顯示後被 API 或另一個選單刪掉，等同清單已變
        logger.info(
            "/%s 按鈕已過期（user=%s, data=%s, 目前版本=%s, %s）",
            COMMAND, callback_query.from_user.id, callback_query.data, ctx.version if ctx else None,
            type(error).__name__,
        )
        await callback_query.answer(_STALE_ALERT, show_alert=True)
        return
    except RouteStoreUnavailableError as error:
        # 讀不到（無法判斷）與寫入時出錯（結果未知、請重新查看）的訊息由服務層區分，原樣轉達
        await callback_query.answer(f"⚠️ {error.message}"[:ALERT_TEXT_LIMIT], show_alert=True)
        return
    except Exception as error:  # noqa: BLE001 - UI 邊界：記錄完整 traceback 並回報給管理員
        logger.exception(
            "/%s 選單操作失敗（user=%s, data=%s, 用途=%s）",
            COMMAND, callback_query.from_user.id, callback_query.data, _target_key(ctx, callback_query.data),
        )
        await callback_query.answer(
            f"操作失敗：{type(error).__name__}: {error}（詳見後端 log）"[:ALERT_TEXT_LIMIT], show_alert=True
        )
        return
    await callback_query.answer()


def _target_key(ctx: MenuContext | None, data: str) -> str | None:
    """盡量從 callback data 找出操作的用途鍵（只用於 log）。Best-effort key for logging."""
    parts = data.split(":")
    if ctx is None or len(parts) < 4:
        return None
    try:
        return ctx.key_at(int(parts[3])).key
    except (ValueError, StaleMenuError):
        return None


async def _dispatch(
    callback_query: CallbackQuery,
    ctx: MenuContext,
    views: list[KeyView],
    action: Action,
    args: list[str],
    app: Any = None,
) -> None:
    """依動作切換畫面或寫入（參數個數已由 :func:`parse_callback` 檢查）。

    Switch screens or write, by action (arity already checked).

    Raises:
        StaleMenuError: 參數無效。
    """
    count = len(args) - 2 if action in _TOKEN_ARGS else len(args)
    n = [_index(arg) for arg in args[:count]]
    if action is Action.LIST:
        await _edit(callback_query, render_list(ctx, views, n[0]))
    elif action is Action.DETAIL:
        key_view = await capability_route_service.describe(ctx.key_at(n[0]).key)
        await _edit(callback_query, render_detail(ctx, n[0], key_view))
    elif action is Action.CAPABILITY:
        capability = ctx.capability_at(n[0], n[1])
        view = (await capability_route_service.describe(ctx.key_at(n[0]).key)).view(capability)
        if view is None:
            raise StaleMenuError
        await _edit(callback_query, render_capability(ctx, n[0], n[1], view))
    elif action is Action.PROVIDERS:
        await _edit(callback_query, render_providers(ctx, n[0], n[1]))
    elif action is Action.MODELS:
        await _edit(callback_query, render_models(ctx, n[0], n[1], n[2]))
    elif action is Action.EFFORTS:
        await _edit(callback_query, render_efforts(ctx, n[0], n[1], n[2], n[3]))
    elif action is Action.CONFIRM:
        await _edit(callback_query, render_confirm(ctx, n[0], n[1], n[2], args[3], args[4]))
    elif action is Action.SAVE:
        await _save(callback_query, ctx, n[0], n[1], n[2], args[3], args[4], app)
    elif action is Action.DELETE:
        await _delete(callback_query, ctx, n[0], n[1], app)


async def _show_after_write(
    callback_query: CallbackQuery,
    build_screen: Callable[[], Awaitable[tuple[str, InlineKeyboardMarkup]]],
    done: str,
    key: str,
) -> None:
    """寫入已提交後組出並顯示結果畫面；任何一步失敗都明說「已完成」，不讓外層報成操作失敗。

    Build and show the result of a committed write. If either step fails,
    say the write succeeded instead of letting it surface as a failure.

    Raises:
        _AlreadyAnswered: 畫面組不出或更新失敗、已改以提示回報。
    """
    try:
        await _edit(callback_query, await build_screen())
    except Exception as error:  # noqa: BLE001 - 寫入已提交：只能回報顯示失敗
        logger.exception(
            "/%s：%s 已%s，但更新選單訊息失敗（user=%s, data=%s）",
            COMMAND, key, done, callback_query.from_user.id, callback_query.data,
        )
        try:
            await callback_query.answer(
                f"✅ 已{done}，但選單無法更新（{type(error).__name__}）；請重新輸入 /{COMMAND} 查看。"[:ALERT_TEXT_LIMIT],
                show_alert=True,
            )
        except TelegramAPIError as answer_error:
            # 回呼查詢可能已逾時、或網路錯誤：寫入仍已完成，不讓外層再記一次「操作失敗」
            logger.warning("/%s：%s 已%s，但連提示都送不出去：%s", COMMAND, key, done, answer_error)
        raise _AlreadyAnswered from error


async def _refresh_global_client_if(key: str, capability: str, app: Any) -> None:
    """改的是全域 ``*`` 的 ``llm`` 時重建診斷用的全域客戶端（與 API 相同，模型路由計畫 D9）。

    寫入或刪除後不論成敗都呼叫（結果未知時 DB 可能已改）。``app`` 由 dispatcher 注入
    （``main.py`` 的 ``dp["app"]``）；沒有時無法重建，記 WARNING——不然診斷端點會一直停在舊的
    全域設定，卻沒有任何線索。

    Rebuild the diagnostics' global client after any attempt to change the
    global ``llm`` row; warn when the app was not injected.
    """
    if key != GLOBAL_ROUTE_KEY or capability != CAPABILITY_LLM:
        return
    if app is None:
        logger.warning("/%s 改了全域 * 的 llm，但 dispatcher 沒有注入 app，無法重建診斷用的全域客戶端", COMMAND)
        return
    await capability_route_service.refresh_global_client(app.state)


async def _save(
    callback_query: CallbackQuery,
    ctx: MenuContext,
    key_index: int,
    capability_index: int,
    provider_index: int,
    model_token: str,
    effort_token: str,
    app: Any = None,
) -> None:
    key = ctx.key_at(key_index).key
    capability = ctx.capability_at(key_index, capability_index)
    provider, model, effort = resolve_selection(
        ctx, key_index, capability_index, provider_index, model_token, effort_token
    )
    try:
        view = await capability_route_service.set_route(
            key, provider.id, model, effort, updated_by=f"tg:{callback_query.from_user.id}", capability=capability
        )
    except InvalidRouteError as error:
        await _edit(callback_query, render_error(ctx, key_index, capability_index, error.message))
        return
    finally:
        await _refresh_global_client_if(key, capability, app)

    async def screen() -> tuple[str, InlineKeyboardMarkup]:
        # 寫入只接受已宣告的鍵與能力，不會改變清單（只有刪除孤兒設定才會），沿用同一個選單版本
        return render_result(ctx, key_index, "✅ 已儲存", view)

    await _show_after_write(callback_query, screen, "儲存", key)


async def _delete(
    callback_query: CallbackQuery, ctx: MenuContext, key_index: int, capability_index: int, app: Any = None
) -> None:
    key = ctx.key_at(key_index).key
    capability = ctx.capability_at(key_index, capability_index)
    try:
        result = await capability_route_service.delete_route(
            key, updated_by=f"tg:{callback_query.from_user.id}", capability=capability
        )
    finally:
        await _refresh_global_client_if(key, capability, app)
    if not result.deleted:
        title = "ℹ️ 這個能力本來就沒有設定"
    elif result.view is None and not result.view_unavailable:
        # 孤兒設定刪除後已從清單消失，沒有「預設」可言
        title = f"🗑️ 已刪除 <b>{html.escape(key)}</b> 的 {_title(capability)} 設定"
    else:
        title = "↩️ 已恢復預設"

    async def screen() -> tuple[str, InlineKeyboardMarkup]:
        # 刪除孤兒設定會讓清單變短、版本改變：用重新載入的選單產生按鈕，並把序號夾在範圍內，
        # 「返回清單」才會落在有效的頁
        new_ctx = await load_context()
        return render_result(
            new_ctx, min(key_index, len(new_ctx.keys) - 1), title, result.view,
            view_unavailable=result.view_unavailable,
        )

    await _show_after_write(callback_query, screen, "恢復預設", key)
