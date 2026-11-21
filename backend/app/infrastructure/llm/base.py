"""Abstract base for LLM clients (plan D2).

LLM 客戶端抽象基底（計畫 D2）。

The shared contract and shared logic of every text LLM provider:

- ``generate_structured_data`` is the only public generation method (``@abstractmethod``)
- unified ``model`` resolution (override > ``LLM_MODEL_NAME``) and effort-support checks
- the public ``model_label`` property replaces cross-layer reads of ``_formatted_model_name``
- ``_retry_transient()`` is the transient-error retry shared by HTTP-based providers

所有文字 LLM provider 的共同契約與共用邏輯：

- ``generate_structured_data`` 為唯一公開生成方法（``@abstractmethod``）
- 統一的 ``model`` 解析（覆寫 > ``LLM_MODEL_NAME``）與 effort 支援檢查
- ``model_label`` 公開屬性取代跨層讀取 ``_formatted_model_name``
- ``_retry_transient()`` 供 HTTP 類 provider 共用的瞬時錯誤重試

An ABC is chosen over a Protocol for the same reasons as ``audio_evaluator/base.py``: subclasses must be forced
to implement, the base holds concrete shared logic, and the factory and the diagnostics service need
``isinstance`` dispatch.

選 ABC 而非 Protocol 的理由與 ``audio_evaluator/base.py`` 一致：要強制子類
實作、基底要放具體共用邏輯、工廠與診斷服務需要 ``isinstance`` 分流。

This module deliberately has zero third-party dependencies (it imports neither openai, jsonschema, nor
google-genai), so no provider's dependency becomes a startup prerequisite of another provider through the base.

本模組刻意零第三方依賴（不 import openai / jsonschema / google-genai），
確保任何 provider 的依賴都不會因為基底而變成其他 provider 的啟動前提。
"""

import abc
import asyncio
import enum
import logging
from collections.abc import Awaitable, Callable
from typing import ClassVar, Final, TypeAlias, TypeVar

from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.infrastructure.llm.endpoints import Endpoint
from app.infrastructure.llm.labels import build_model_label, normalize_provider
from app.schemas.llm.base import LLMGenerateResult

logger = logging.getLogger(__name__)

T = TypeVar("T")



class EffortMode(enum.Enum):
    """Special values for the effort argument.

    effort 參數的特殊值。
    """

    OMIT = "omit"


# 明確「不使用 effort」（白名單中不帶 @effort 的模型）。與 None 不同：None 是「沿用
# 該 provider 在 .env 的預設」，OMIT_EFFORT 是「不傳」——CLI 類收到時命令列省略
# --effort，不支援 effort 的 provider 視同 None。刻意用 Enum 而非空字串：使用者從
# API 傳進來的任何字串（包括 ""）都不可能等於它，照舊走 effort 驗證並被拒絕。
# Explicit "no effort", distinct from None ("use the configured default").
# An Enum member, so no user-supplied string (not even "") can collide with it.
OMIT_EFFORT: Final = EffortMode.OMIT

# effort 參數的型別：明確值、OMIT_EFFORT（不傳），或 None（沿用預設）
EffortArg: TypeAlias = str | EffortMode | None


class BaseLLMClient(abc.ABC):
    """Abstract base for text LLM clients.

    文字 LLM 客戶端的抽象基底。

    Attributes:
        PROVIDER_NAME: the factory registry key, also the ``(provider)`` part of the label
            工廠註冊鍵，也是標籤的 ``(provider)`` 段
        SUPPORTS_EFFORT: whether an ``effort`` override is accepted; when ``False``, giving a value raises. The
            factory registry holds a separate static declaration, checked for consistency at load time.
            是否接受 ``effort`` 覆寫；False 時給值即拋錯。
            工廠註冊表另有一份靜態宣告，載入時核對必須一致。
        MODEL_LABEL_STRIP_PREFIX: the model-name prefix removed in labels
            標籤中要去掉的模型名前綴
        ENDPOINT_TYPES: the endpoint types this class accepts (see ``endpoints.ALL_TYPES``); checked at
            construction
            本類可接受的端點類型（見 ``endpoints.ALL_TYPES``）；建構時檢查
        _model_name: the model name actually sent upstream
            實際送給後端的模型名
        _provider: the endpoint ID; the normalized ``LLM_PROVIDER`` when no endpoint is given
            端點 ID；未指定端點時為正規化後的 ``LLM_PROVIDER``
        _endpoint: the endpoint given at construction; ``None`` means reading the legacy single-set settings
            建立時指定的端點；None 表示讀舊式單組設定
        _effort: the effective effort; ``None`` for providers without that notion
            生效的 effort；無此概念的 provider 為 None
    """

    PROVIDER_NAME: ClassVar[str] = ""
    SUPPORTS_EFFORT: ClassVar[bool] = False
    MODEL_LABEL_STRIP_PREFIX: ClassVar[str] = ""
    # 本類可接受的端點類型（見 endpoints.ALL_TYPES）；建構時檢查，避免例如
    # Gemini 客戶端拿到中轉站端點、用錯金鑰打錯服務
    ENDPOINT_TYPES: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        *,
        model: str | None = None,
        effort: EffortArg = None,
        endpoint: Endpoint | None = None,
    ) -> None:
        """Resolve the model name and validate effort support.

        解析模型名並檢查 effort 是否受支援。

        Args:
            model: the model-name override; ``None`` keeps ``LLM_MODEL_NAME``
                覆寫模型名；None 沿用 ``LLM_MODEL_NAME``
            effort: the thinking-depth override; giving a value to an unsupported provider raises;
                :data:`OMIT_EFFORT` means not sending one
                覆寫思考深度；不支援的 provider 給值即拋錯；``OMIT_EFFORT`` 表示不傳
            endpoint: the endpoint (source of the URL, the key, and the label prefix); its type must be in
                ``ENDPOINT_TYPES``. ``None`` keeps the legacy single-set settings (``LLM_PROVIDER`` and so on),
                the same behavior as before endpoints were introduced. When giving an endpoint, ``model``
                should usually be given too: ``None`` still takes ``LLM_MODEL_NAME`` and ignores the endpoint's
                allowlist.
                端點（網址、金鑰、標籤前綴的來源），類型須在
                ``ENDPOINT_TYPES`` 內；None 沿用舊式單組設定（``LLM_PROVIDER``
                等），與導入端點之前的行為相同。指定端點時通常應一併指定
                ``model``：None 仍取 ``LLM_MODEL_NAME``，不看端點的白名單。

        Raises:
            LLMServiceError: if the endpoint type mismatches, effort is unsupported, or the model name is empty
                若端點類型不符、effort 不受支援、或模型名為空
        """
        self._check_endpoint_type(endpoint)
        self._endpoint = endpoint
        self._provider = endpoint.id if endpoint is not None else normalize_provider(settings.LLM_PROVIDER)
        self._reject_unsupported_effort(effort)

        self._model_name = (model or settings.LLM_MODEL_NAME or "").strip()
        if not self._model_name:
            raise LLMServiceError("模型名為空：LLM_MODEL_NAME 未設定且未提供覆寫。")

        # 支援 effort 的子類在自己的驗證後填入
        # Subclasses that support effort assign this after validation.
        self._effort: str | None = None

    # ------------------------------------------------------------------
    # 公開屬性（Public attributes）
    # ------------------------------------------------------------------

    @property
    def model_label(self) -> str:
        """The label written to Anki tags and dedup records.

        寫入 Anki tag 與去重紀錄的標籤。
        """
        return build_model_label(
            self._provider,
            self._model_name,
            self._effort,
            strip_prefix=self.MODEL_LABEL_STRIP_PREFIX,
        )

    @property
    def _formatted_model_name(self) -> str:
        """Transitional alias of ``model_label``; removed once the existing readers migrate.

        ``model_label`` 的過渡 alias；既有讀取點遷移完畢後移除。
        """
        return self.model_label

    @classmethod
    def default_effort(cls) -> str | None:
        """Return the provider's default effort when no override is given.

        回傳未帶覆寫時該 provider 的預設 effort。

        Returns:
            the default effort; ``None`` for providers without that notion
            預設 effort；無此概念回 None
        """
        return None

    @property
    def provider(self) -> str:
        """The endpoint ID, source of the label prefix (google / openai / empty string add no prefix).

        端點 ID，標籤前綴的來源（google / openai / 空字串不加前綴）。
        """
        return self._provider

    @property
    def endpoint(self) -> Endpoint | None:
        """The endpoint given at construction; ``None`` means the legacy single-set settings.

        建立時指定的端點；None＝舊式單組設定。
        """
        return self._endpoint

    @property
    def model_name(self) -> str:
        """The model name actually sent upstream.

        實際送給後端的模型名。
        """
        return self._model_name

    @property
    def effort(self) -> str | None:
        """The effective effort; ``None`` for providers without that notion.

        生效的 effort；無此概念的 provider 為 None。
        """
        return self._effort

    # ------------------------------------------------------------------
    # 抽象契約（Abstract contract）
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def generate_structured_data(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> LLMGenerateResult:
        """Call the LLM and obtain JSON data that strictly conforms to ``response_schema``.

        呼叫 LLM 並取得嚴格符合 ``response_schema`` 的 JSON 資料。

        Args:
            system_prompt: the system prompt
                系統提示
            user_prompt: the user input
                使用者輸入
            response_schema: the JSON Schema definition dict
                JSON Schema 定義字典

        Returns:
            an ``LLMGenerateResult`` instance
            ``LLMGenerateResult`` 實例

        Raises:
            LLMServiceError: on any failure; every failure is raised as this class or a subclass
                任何失敗一律以此類（或其子類）拋出
        """

    # ------------------------------------------------------------------
    # 共用 helper（Shared helpers）
    # ------------------------------------------------------------------

    async def _retry_transient(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        is_transient: Callable[[BaseException], bool],
        max_retries: int = 3,
        base_delay: float = 1.0,
        timeout: float | None = None,
        describe_error: Callable[[BaseException], str] | None = None,
    ) -> T:
        """Retry an awaitable on transient errors with exponential backoff and an optional absolute timeout.

        對瞬時錯誤做指數退避重試，並可加絕對超時。

        Modeled on the retry loop in ``audio_evaluator/gemini_client.py``. Non-transient errors are re-raised
        unchanged without retrying; exhausting the retries raises ``LLMServiceError``. An attempt whose own
        timeout expires is always retried, regardless of ``is_transient``.

        藍本為 ``audio_evaluator/gemini_client.py`` 的重試迴圈。非瞬時錯誤
        原樣拋出、不重試；重試耗盡拋 ``LLMServiceError``。單次嘗試自己的超時到期時一律重試，不經 ``is_transient``。

        Args:
            operation: the awaitable factory, called anew for each attempt
                每次嘗試都重新建立的 awaitable 工廠
            is_transient: the predicate deciding whether an exception is worth retrying
                判斷例外是否值得重試
            max_retries: the maximum number of attempts
                最大嘗試次數
            base_delay: the first wait in seconds, doubled after each attempt
                首次等待秒數，之後每次加倍
            timeout: the absolute timeout of a single attempt in seconds; ``None`` sets none
                單次嘗試的絕對超時秒數；None 不設
            describe_error: how the last exception is written into the message when retries are exhausted;
                ``None`` uses "type name: repr" (so exceptions with an empty str, such as ``TimeoutError()``,
                stay identifiable). A provider may pass its own version, for example one that does not embed
                the whole response JSON, because the message reaches the generation scripts, which grade it by
                substring matching.
                重試耗盡時把最後一個例外寫進訊息的方式；None 用
                「型別名: repr」（``TimeoutError()`` 這類 str 為空的例外仍可
                辨識）。provider 可傳入自己的版本，例如不嵌整包回應 JSON——
                訊息會到生成腳本，而腳本以子字串比對分級。

        Returns:
            the result of ``operation``
            ``operation`` 的結果

        Raises:
            LLMServiceError: if the retries are exhausted or the last attempt timed out
                若重試耗盡或超時
        """
        for attempt in range(1, max_retries + 1):
            scope: asyncio.Timeout | None = None
            try:
                if timeout is None:
                    return await operation()
                async with asyncio.timeout(timeout) as scope:
                    return await operation()
            except Exception as error:  # noqa: BLE001 - 由下方分流決定是否重試
                # 只有本函式自己的超時範圍到期才算「逾時」；operation 自己拋出的
                # TimeoutError（socket / SDK 的）與其他例外一樣交給 is_transient 裁決。
                # Python ≥ 3.11 的 asyncio.TimeoutError 就是內建 TimeoutError，
                # 不能靠例外型別區分，必須問 scope.expired()。
                expired = (
                    scope is not None
                    and isinstance(error, TimeoutError)
                    and scope.expired()
                )
                if expired:
                    logger.error(
                        "LLM API 回應超時 (第 %d/%d 次, %s 秒)。", attempt, max_retries, timeout
                    )
                    if attempt == max_retries:
                        raise LLMServiceError(
                            f"LLM API 逾時（{timeout} 秒）且在 {max_retries} 次重試後仍失敗。"
                        ) from error
                else:
                    if not is_transient(error):
                        raise
                    logger.warning(
                        "LLM API 瞬時錯誤 (第 %d/%d 次): %s", attempt, max_retries, error
                    )
                    if attempt == max_retries:
                        description = (
                            describe_error(error)
                            if describe_error is not None
                            else f"{type(error).__name__}: {error!r}"
                        )
                        raise LLMServiceError(
                            f"LLM API 瞬時錯誤在 {max_retries} 次重試後仍失敗: {description}"
                        ) from error
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))

        raise LLMServiceError("LLM API 重試迴圈異常結束。")  # pragma: no cover - 型別保底

    @classmethod
    def _check_endpoint_type(cls, endpoint: Endpoint | None) -> None:
        """Raise when the endpoint's type is not in ``ENDPOINT_TYPES``.

        端點類型不在 ``ENDPOINT_TYPES`` 內時拋錯（工廠之外直接建構時的防線）。

        This is the guard for direct construction outside the factory. ``None`` always passes.

        這是工廠之外直接建構時的防線。``None`` 一律通過。

        Args:
            endpoint: the endpoint to check, or ``None``
                要檢查的端點，或 ``None``

        Raises:
            LLMServiceError: if the type does not match
                若類型不符
        """
        if endpoint is not None and endpoint.type not in cls.ENDPOINT_TYPES:
            raise LLMServiceError(
                f"{cls.__name__} 不能使用 {endpoint.type} 類端點 '{endpoint.id}'"
                f"（可接受：{', '.join(sorted(cls.ENDPOINT_TYPES)) or '無'}）。"
            )

    @classmethod
    def _reject_unsupported_effort(cls, effort: EffortArg) -> None:
        """Raise when ``SUPPORTS_EFFORT`` is ``False`` but an effort is given.

        ``SUPPORTS_EFFORT`` 為 False 卻收到 effort 時拋錯。

        It is a classmethod so subclasses can call it **before** their other checks (keeping the error order from
        before the hierarchy was introduced); the base ``__init__`` calling it again is idempotent. The message
        wording matches the pre-hierarchy ``factory.py`` version; the supported list comes from the providers with
        ``supports_effort=True`` in the factory's static registry (in registration order, following the registry
        as it changes), without importing any provider module. ``None`` and ``OMIT_EFFORT`` are not overrides; an
        empty string is still an invalid override.

        做成 classmethod 是為了讓子類能在自己的其他檢查**之前**先呼叫（保持
        層級化前的錯誤順序），基底 ``__init__`` 再呼叫一次是冪等的。訊息
        句型與層級化前 ``factory.py`` 的版本相同；支援清單取自工廠靜態註冊表中
        ``supports_effort=True`` 的 provider（依註冊順序，隨註冊表增減），
        不 import 任何 provider 模組。None 與 ``OMIT_EFFORT`` 不算覆寫；空字串仍視為非法覆寫。

        Args:
            effort: the effort argument to check
                要檢查的 effort 參數

        Raises:
            LLMServiceError: if effort is not supported but a value is given
                若不支援 effort 卻收到值
        """
        # None 與 OMIT_EFFORT 都不算 effort 覆寫；空字串照舊視為非法覆寫
        if effort is None or effort is OMIT_EFFORT or cls.SUPPORTS_EFFORT:
            return
        # 延遲 import：factory 於模組層 import 本模組，反向必須惰性以免循環；
        # providers_supporting_effort 是純查表，不會載入 provider 模組。
        from app.infrastructure.llm.factory import providers_supporting_effort

        supported = ", ".join(providers_supporting_effort()) or "（無）"
        provider = normalize_provider(settings.LLM_PROVIDER)
        raise LLMServiceError(
            f"effort 覆寫僅 {supported} provider 支援（目前 LLM_PROVIDER='{provider or 'google'}'）。"
        )
