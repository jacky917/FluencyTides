"""
Anki 模型管理與匯入服務模組。

本模組整合了舊版 anki_model_manager.py 與 import_model.py 的職責：
1. 讀取本地模型定義檔 (.json) 中的 LLM Schema 與欄位配置。
2. 將 LLM 結構化輸出組裝為 AnkiConnect addNote 的 Payload。
3. 從本地模型模板檔（.json, _front.html, _back.html, _style.css）
   匯入完整的筆記類型 (Note Type) 至 Anki。
4. 提供牌組存在性檢查與防重複驗證等前置防護功能。

重構自：
- old/Anki/utils/anki_model_manager.py
- old/Anki/utils/import_model.py

設計原則：
- 此模組屬於 Service 層，透過注入 AnkiClient (Infrastructure) 完成
  所有與 AnkiConnect 的通訊，嚴格遵守 Clean Architecture 解耦原則。
- 不自行建立 httpx 連線，所有 HTTP I/O 委託給 AnkiClient。

Anki model management and import service module.

This module merges the responsibilities of the legacy anki_model_manager.py
and import_model.py:
1. Read LLM schemas and field configs from local model definition (.json) files.
2. Assemble LLM structured output into AnkiConnect addNote payloads.
3. Import complete note types into Anki from local template files
   (.json, _front.html, _back.html, _style.css).
4. Provide guard features such as deck existence checks and duplicate
   prevention.

Design principles: this is a Service-layer module; all AnkiConnect
communication is delegated to the injected AnkiClient (Infrastructure),
strictly following Clean Architecture decoupling. It never creates its
own httpx connections.
"""

import json
import logging
from pathlib import Path

from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.schemas.anki import (
    AnkiModelInfo,
    AnkiNote,
    AnkiNoteOptions,
)

logger = logging.getLogger(__name__)


class AnkiModelManager:
    """管理 Anki 模型定義檔的讀取、LLM 輸出組裝與 AnkiConnect 提交。

    Manages reading Anki model definition files, assembling LLM output,
    and submitting notes through AnkiConnect.

    此類別封裝了以下核心能力：
    - 從 anki_models/ 目錄讀取 JSON Schema（供 LLM 結構化輸出使用）。
    - 將 LLM 回傳的 JSON 資料轉換為 AnkiNote Pydantic 模型。
    - 透過 AnkiClient 提交新卡片到 Anki，包含完整的重複檢測與錯誤追蹤。
    - 匯入本地模型模板至 Anki（createModel）。

    Core capabilities: read JSON schemas from anki_models/ (for LLM
    structured output), convert LLM JSON into AnkiNote Pydantic models,
    submit new cards via AnkiClient with duplicate detection and error
    tracing, and import local model templates into Anki (createModel).

    Attributes:
        _model_dir: 存放 JSON 模型定義檔的資料夾路徑。Directory containing
            JSON model definition files.
        _anki_client: 注入的非同步 AnkiConnect 客戶端。Injected async
            AnkiConnect client.
    """

    #: 預設模型目錄：錨定於套件位置（backend/app/anki_models）的絕對路徑。
    #: 舊版預設為相對 CWD 的 "./anki_models"，從其他目錄啟動時會在
    #: 錯誤位置讀取（並自動建立）空的 anki_models 資料夾。
    DEFAULT_MODEL_DIR: Path = Path(__file__).resolve().parents[1] / "anki_models"

    def __init__(
        self,
        anki_client: AnkiClient,
        model_dir: str | Path | None = None,
    ) -> None:
        """初始化管理器。

        Initialize the manager.

        Args:
            anki_client: 注入的 AnkiClient 實例，用於所有 AnkiConnect 通訊。
                Injected AnkiClient instance used for all AnkiConnect I/O.
            model_dir: 存放模型定義檔的資料夾路徑；未提供時使用
                DEFAULT_MODEL_DIR（絕對路徑，不受啟動 CWD 影響）。
                Directory of model definition files; defaults to
                DEFAULT_MODEL_DIR (absolute, CWD-independent).

        Note:
            目錄不存在時只記 warning、不再自動建立——本類別是模型資產的
            讀取者，舊版的自動 mkdir 曾在錯誤的 CWD 下生出空資料夾。
            When the directory is missing only a warning is logged; it is
            no longer auto-created, since this class is a reader of model
            assets and the legacy auto-mkdir once created empty folders
            under the wrong CWD.
        """
        self._model_dir = (
            Path(model_dir) if model_dir is not None else self.DEFAULT_MODEL_DIR
        )
        self._anki_client = anki_client

        if not self._model_dir.exists():
            logger.warning(
                "模型資料夾不存在: %s（將視為無可用模型；"
                "請確認部署資產或 model_dir 參數是否正確）",
                self._model_dir,
            )

    # ========================================================================
    # Schema 讀取
    # ========================================================================

    def get_model_schema(self, model_file_name: str) -> dict[str, object]:
        """從本地讀取 Anki 模型定義 JSON Schema。

        Read an Anki model definition JSON schema from local disk.

        支援兩種格式：
        1. 根目錄即為 JSON Schema（簡易模型，如 AI_QA_Dark.json）。
        2. 包含 'llm_schema' 鍵值的複合定義檔（如 TOEIC_Coach_Dark.json），
           此時會提取 llm_schema 子物件作為 LLM 的 response_format。

        Two formats are supported: (1) the file root itself is the JSON
        schema; (2) a composite definition containing an 'llm_schema'
        key, in which case that sub-object is extracted as the LLM
        response_format.

        Args:
            model_file_name: JSON 檔案名稱，例如 'TOEIC_Coach_Dark.json'。
                JSON file name, e.g. 'TOEIC_Coach_Dark.json'.

        Returns:
            包含 JSON Schema 定義的字典，可直接用於 LLM 的 response_format
            參數。Dict with the JSON schema, usable directly as the LLM
            response_format parameter.

        Raises:
            FileNotFoundError: 找不到對應檔案時。When the file is missing.
            ValueError: JSON 格式無效時。When the JSON is invalid.
        """
        file_path = self._model_dir / model_file_name
        if not file_path.is_file():
            logger.error("找不到名為 %s 的模型定義檔。", file_path)
            raise FileNotFoundError(f"找不到檔案: {file_path}")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data: dict[str, object] = json.load(f)

                # 判斷是否為複合定義檔（含有 llm_schema 巢狀結構）
                if "llm_schema" in data and isinstance(
                    data["llm_schema"], dict
                ):
                    logger.info(
                        "檢測到複合模型定義檔，提取 llm_schema: %s",
                        model_file_name,
                    )
                    return dict(data["llm_schema"])

                logger.info(
                    "讀取標準模型 Schema: %s", model_file_name
                )
                return data

        except json.JSONDecodeError as decode_error:
            logger.error(
                "模型定義檔 %s 無效，請檢查 JSON 格式。", file_path
            )
            raise ValueError(f"無效的 JSON 檔案: {decode_error}")

    def get_model_fields(self, model_file_name: str) -> list[str]:
        """從模型定義檔中讀取 inOrderFields 欄位清單。

        Read the ordered inOrderFields list from a model definition file.

        Args:
            model_file_name: JSON 檔案名稱。JSON file name.

        Returns:
            欄位名稱字串列表，按順序排列。Ordered list of field names.

        Raises:
            FileNotFoundError: 找不到對應檔案時。When the file is missing.
            ValueError: JSON 格式無效或缺少 inOrderFields 時。When JSON is
                invalid or 'inOrderFields' is absent.
        """
        file_path = self._model_dir / model_file_name
        if not file_path.is_file():
            raise FileNotFoundError(f"找不到檔案: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            data: dict[str, object] = json.load(f)

        if "inOrderFields" not in data or not isinstance(
            data["inOrderFields"], list
        ):
            raise ValueError(
                f"模型定義檔 {model_file_name} 缺少 'inOrderFields' 陣列"
            )

        return [str(item) for item in data["inOrderFields"]]

    # ========================================================================
    # LLM 輸出 -> AnkiNote 組裝
    # ========================================================================

    def create_note_from_llm_response(
        self,
        deck_name: str,
        model_name: str,
        llm_response: dict[str, object],
        tags: list[str] | None = None,
    ) -> AnkiNote:
        """將 LLM 結構化輸出轉換為 AnkiNote Pydantic 模型。

        Convert LLM structured output into an AnkiNote Pydantic model.

        對於 LLM 回傳的非字串欄位（如陣列、物件），會透過
        json.dumps 序列化為合法 JSON 字串，讓 Anki 卡片內的
        JavaScript 能夠正確解析與渲染。

        Non-string fields (arrays/objects) returned by the LLM are
        serialized with json.dumps into valid JSON strings so the card
        template's JavaScript can parse and render them.

        Args:
            deck_name: 目標牌組名稱。Target deck name.
            model_name: 目標模型（筆記類型）名稱。Target model (note type)
                name.
            llm_response: LLM 回傳的結構化 JSON 字典。Structured JSON dict
                returned by the LLM.
            tags: 附加至卡片的標籤列表。Tags attached to the card.

        Returns:
            驗證後的 AnkiNote Pydantic 模型實例。Validated AnkiNote model
            instance.
        """
        # 利用 json.dumps 確保非字串欄位被轉為合法 JSON 字串，
        # 這是因為 Anki 的欄位只接受純字串，而卡片模板中的 JS
        # 會透過 JSON.parse() 解析這些序列化後的結構化資料。
        fields_data: dict[str, str] = {}
        for key, value in llm_response.items():
            if isinstance(value, (dict, list)):
                fields_data[str(key)] = json.dumps(
                    value, ensure_ascii=False
                )
            else:
                fields_data[str(key)] = str(value)

        options = AnkiNoteOptions(
            allowDuplicate=False,
            duplicateScope="deck",
            duplicateScopeOptions={
                "deckName": deck_name,
                "checkChildren": False,
                "checkAllModels": False,
            },
        )

        note = AnkiNote(
            deckName=deck_name,
            modelName=model_name,
            fields=fields_data,
            tags=tags or [],
            options=options,
        )

        logger.debug("成功建立 AnkiNote 模型: %s", model_name)
        return note

    # ========================================================================
    # AnkiConnect 提交操作
    # ========================================================================

    async def submit_note(self, note: AnkiNote) -> int:
        """提交 AnkiNote 到 AnkiConnect，並包含重複卡片的智能偵測。

        Submit an AnkiNote via AnkiConnect with smart duplicate detection.

        當 AnkiConnect 回報 duplicate 錯誤時，會自動反查重複卡片
        所在的牌組，提供詳細的錯誤訊息幫助使用者定位問題。

        When AnkiConnect reports a duplicate error, the deck containing
        the duplicate is looked up to enrich the error message.

        Args:
            note: 已驗證的 AnkiNote 模型實例。Validated AnkiNote instance.

        Returns:
            成功建立後 Anki 回傳的筆記 ID。Note ID returned by Anki.

        Raises:
            AnkiConnectError: AnkiConnect 回傳錯誤時（包含增強的重複偵測
                資訊）。On AnkiConnect error (with enriched duplicate info).
            RuntimeError: AnkiConnect 回傳 null result 時。When AnkiConnect
                returns a null result.
        """
        logger.info(
            "準備提交筆記至 [%s] 牌組，模型: [%s]",
            note.deckName,
            note.modelName,
        )

        try:
            result = await self._anki_client.add_note(note)
        except AnkiConnectError as e:
            # 若為 duplicate 錯誤，嘗試反查重複卡片所在牌組
            if "duplicate" in e.message.lower():
                detailed = await self._lookup_duplicate_location(note)
                if detailed:
                    logger.error(detailed)
                    raise AnkiConnectError(detailed)
            raise

        if result is None:
            raise RuntimeError(
                "AnkiConnect returned null result without error message."
            )

        logger.info("提交成功，獲取筆記 ID: %d", result)
        return result

    async def _lookup_duplicate_location(
        self, note: AnkiNote
    ) -> str | None:
        """當新增筆記遭遇重複錯誤時，反查重複卡片的所在牌組。

        Look up the deck holding the duplicate note after a duplicate error.

        此方法透過搜尋第一個欄位的值來定位重複的筆記，
        並追溯其所屬的牌組名稱，幫助使用者快速找到衝突來源。

        Locates the duplicate by searching the first field's value and
        traces its deck names to help the user find the conflict source.

        Args:
            note: 觸發重複錯誤的筆記。The note that triggered the error.

        Returns:
            包含重複卡片詳細位置的錯誤訊息字串，若查找失敗則回傳 None。
            Detailed error message string, or None if lookup fails.
        """
        try:
            if not note.fields:
                return None

            first_field_val = next(iter(note.fields.values()))
            note_ids = await self._anki_client.find_notes(
                f'"{first_field_val}"'
            )

            if not note_ids:
                return None

            notes_info = await self._anki_client.get_notes_info(
                notes=note_ids
            )
            decks: set[str] = set()

            for n_info in notes_info:
                if n_info.cards:
                    cards_info = await self._anki_client.get_cards_info(
                        [n_info.cards[0]]
                    )
                    if cards_info and isinstance(cards_info[0], dict):
                        deck_name = cards_info[0].get("deckName")
                        if deck_name:
                            decks.add(str(deck_name))

            if decks:
                deck_list_str = ", ".join(decks)
                return (
                    f"單字/內容 '{first_field_val}' 已經存在！\n"
                    f"👉 本地 Anki 搜尋: nid:{note_ids[0]}\n"
                    f"👉 所屬牌組: [{deck_list_str}]"
                )

        except Exception as lookup_err:
            logger.warning(
                "嘗試反查重複卡片所在牌組時失敗: %s", str(lookup_err)
            )

        return None

    # ========================================================================
    # 牌組存在性檢查
    # ========================================================================

    async def ensure_deck_exists(self, deck_name: str) -> None:
        """檢查目標牌組是否存在，若否則自動建立。

        Ensure the target deck exists, creating it automatically otherwise.

        設計為在發送昂貴的 LLM 請求之前呼叫，避免生成完卡片內容
        後才發現目標牌組不存在的窘境。

        Intended to run before expensive LLM requests, so we never
        generate card content only to find the deck missing.

        Args:
            deck_name: 預期所在的目標牌組名稱。Target deck name.

        Raises:
            RuntimeError: 無法自動建立目標牌組時。When the deck cannot be
                created automatically.
        """
        logger.info("檢查目標牌組是否存在: %s", deck_name)

        decks = await self._anki_client.get_deck_names()
        if deck_name in decks:
            return

        logger.warning(
            "牌組 '%s' 不存在，即將自動建立...", deck_name
        )

        try:
            await self._anki_client.create_deck(deck=deck_name)
            logger.info("✅ 成功建立牌組 '%s'", deck_name)
        except Exception as e:
            err_msg = (
                f"無法自動建立目標牌組 '{deck_name}'！錯誤: {e}"
            )
            logger.error(err_msg)
            raise RuntimeError(err_msg)

    # ========================================================================
    # 防重複前置檢查
    # ========================================================================

    async def can_add_note(
        self,
        deck_name: str,
        model_name: str,
        fields: dict[str, str],
    ) -> bool:
        """檢查給定筆記是否可以安全新增（不重複且牌組/模型皆存在）。

        Check whether the note can be added safely (no duplicate; deck and
        model both exist).

        設計為在發送昂貴的 LLM 請求之前呼叫，先確認 Anki 的存取性
        以及避免重複生卡，節省 API 成本。

        Intended to run before expensive LLM requests, confirming Anki
        accessibility and avoiding duplicate generation to save API cost.

        Args:
            deck_name: 目標牌組名稱。Target deck name.
            model_name: 目標模型名稱。Target model name.
            fields: 用來檢測的欄位字典，建議至少包含 Primary Field。Fields
                used for the check; should contain at least the primary
                field.

        Returns:
            True 若可以新增，False 若為重複卡片或遇到異常。True if addable,
            False if duplicate or on anomaly.
        """
        logger.info(
            "先期檢查是否可以新增筆記: [%s] at [%s]",
            model_name,
            deck_name,
        )
        
        # 取得模型所有定義的欄位，並補齊空字串
        # AnkiConnect 的 canAddNotes 若發現欄位缺失，會直接回傳 False (被誤認為重複)
        full_fields = {}
        all_models = self.list_available_models()
        target_model = next((m for m in all_models if m.model_name == model_name), None)
        if target_model and target_model.fields:
            for f in target_model.fields:
                full_fields[f] = ""
        
        # 覆蓋傳入的測試欄位 (通常是 Primary Field)
        full_fields.update(fields)

        notes_to_check: list[dict[str, object]] = [
            {
                "deckName": deck_name,
                "modelName": model_name,
                "fields": full_fields,
                "options": {
                    "allowDuplicate": False,
                    "duplicateScope": "deck",
                    "duplicateScopeOptions": {
                        "deckName": deck_name,
                        "checkChildren": False,
                        "checkAllModels": False,
                    },
                },
            }
        ]

        results = await self._anki_client.can_add_notes(notes_to_check)
        if results and len(results) > 0:
            can_add = bool(results[0])
            if not can_add:
                logger.warning(
                    "Anki 拒絕新增此卡片 (可能為重複或模型設定不符)。"
                )
            return can_add
        return False



    # ========================================================================
    # 模型查詢（Model Discovery）
    # ========================================================================

    def list_available_models(self) -> list[AnkiModelInfo]:
        """掃描模型定義目錄，回傳所有可用模型的摘要資訊。

        Scan the model definition directory and summarize available models.

        逐一讀取 anki_models/ 目錄下的 .json 檔案，
        解析其 modelName、inOrderFields 與 llm_schema 欄位，
        組裝為 AnkiModelInfo Pydantic 模型列表。

        Reads each .json under anki_models/, parses modelName,
        inOrderFields and llm_schema, and assembles AnkiModelInfo models.

        Returns:
            AnkiModelInfo 模型實例列表，按檔名排序。List of AnkiModelInfo
            instances, sorted by file name.
        """
        models: list[AnkiModelInfo] = []

        if not self._model_dir.exists():
            logger.warning("模型目錄不存在: %s", self._model_dir)
            return models

        for json_path in sorted(self._model_dir.glob("*.json")):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data: dict[str, object] = json.load(f)

                model_name = str(data.get("modelName", json_path.stem))
                fields_raw = data.get("inOrderFields", [])
                fields = (
                    [str(item) for item in fields_raw]
                    if isinstance(fields_raw, list)
                    else []
                )
                has_llm_schema = (
                    "llm_schema" in data
                    and isinstance(data["llm_schema"], dict)
                )

                models.append(
                    AnkiModelInfo(
                        model_name=model_name,
                        model_file_name=json_path.name,
                        fields=fields,
                        has_llm_schema=has_llm_schema,
                    )
                )
            except (json.JSONDecodeError, OSError) as err:
                logger.warning(
                    "解析模型定義檔 %s 時發生錯誤，跳過: %s",
                    json_path.name,
                    err,
                )

        logger.info("掃描完成，共發現 %d 個可用模型定義。", len(models))
        return models

    def get_model_detail(
        self, model_file_name: str
    ) -> dict[str, object]:
        """取得單一模型的完整定義資訊。

        Get the full definition of a single model.

        回傳 JSON 定義檔的完整內容，包含 modelName、inOrderFields、
        llm_schema 等所有欄位。

        Returns the complete content of the JSON definition file,
        including modelName, inOrderFields, llm_schema, etc.

        Args:
            model_file_name: JSON 檔案名稱，例如 'TOEIC_Coach_Dark.json'。
                JSON file name, e.g. 'TOEIC_Coach_Dark.json'.

        Returns:
            模型定義檔的完整 JSON 字典。Full JSON dict of the definition.

        Raises:
            FileNotFoundError: 找不到對應檔案時。When the file is missing.
            ValueError: JSON 格式無效時。When the JSON is invalid.
        """
        file_path = self._model_dir / model_file_name
        if not file_path.is_file():
            logger.error("找不到名為 %s 的模型定義檔。", file_path)
            raise FileNotFoundError(f"找不到檔案: {file_path}")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data: dict[str, object] = json.load(f)
            logger.info("讀取模型詳情: %s", model_file_name)
            return data
        except json.JSONDecodeError as decode_error:
            logger.error(
                "模型定義檔 %s 無效，請檢查 JSON 格式。", file_path
            )
            raise ValueError(f"無效的 JSON 檔案: {decode_error}")

    # ========================================================================
    # AnkiWeb 同步
    # ========================================================================

    async def sync_to_ankiweb(self) -> None:
        """請求 Anki 桌面端執行同步至 AnkiWeb 動作。

        Ask the Anki desktop client to sync with AnkiWeb.

        Raises:
            AnkiConnectError: 同步失敗時。When the sync fails.
        """
        logger.info("正在請求 Anki 執行同步作業 (Sync)...")
        await self._anki_client.sync()
        logger.info("AnkiWeb 同步指令已成功發送並完成。")
