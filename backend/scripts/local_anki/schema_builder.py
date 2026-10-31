"""Anki 筆記模型結構 (Schema) 建置工具模組。

Anki note model schema building utilities for developer scripts;
wraps destructive AnkiConnect schema operations with safety checks.
"""

import json
import logging
from pathlib import Path

from app.infrastructure.anki.client import AnkiClient, AnkiConnectError

logger = logging.getLogger(__name__)


def _read_card_templates(
    base_path: Path, model_name: str, data: dict[str, object]
) -> tuple[list[dict[str, str]], str]:
    """讀出模型的卡片模板與 CSS。

    Reads a model's card templates and CSS.

    定義檔有 ``cardTemplates``（``[{"Name", "Front", "Back"}]``，Front/Back 為檔名）時照它讀，
    可以有多種卡片；``css`` 為 CSS 檔名。沒有時沿用單一卡片的慣例：
    ``{model_name}_front.html``、``{model_name}_back.html``，模板名 ``Card 1``；CSS 為
    ``{model_name}_style.css``。

    With ``cardTemplates`` in the definition, its file names are used (several
    card types allowed); otherwise the single-card convention applies.

    Args:
        base_path: 模型資料夾。The model asset directory.
        model_name: 模型名（找預設檔名用）。The model name.
        data: 已讀入的 JSON 定義（可為空 dict）。The parsed definition.

    Returns:
        ``({"Name", "Front", "Back"} 模板清單（內容為 HTML）, CSS 內容)``。
        The templates with HTML contents, and the CSS.

    Raises:
        FileNotFoundError: 任一模板或 CSS 檔丟失時。When a file is missing.
        ValueError: ``cardTemplates`` 格式錯誤時。When ``cardTemplates`` is malformed.
    """
    raw_templates = data.get("cardTemplates")
    if raw_templates is None:
        specs = [{"Name": "Card 1", "Front": f"{model_name}_front.html", "Back": f"{model_name}_back.html"}]
    elif isinstance(raw_templates, list) and raw_templates:
        specs = []
        for item in raw_templates:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("Name", "Front", "Back")):
                raise ValueError("cardTemplates 每一項都要有字串的 Name、Front、Back。")
            specs.append({"Name": item["Name"], "Front": item["Front"], "Back": item["Back"]})
    else:
        raise ValueError("cardTemplates 必須是非空陣列。")
    css_name = data.get("css") if isinstance(data.get("css"), str) else f"{model_name}_style.css"

    def read(name: str) -> str:
        path = base_path / name
        if not path.is_file():
            logger.error("缺少必要匯入文件：%s", path)
            raise FileNotFoundError(f"找不到檔案: {path}")
        return path.read_text(encoding="utf-8")

    templates = [{"Name": spec["Name"], "Front": read(spec["Front"]), "Back": read(spec["Back"])} for spec in specs]
    return templates, read(str(css_name))


class AnkiSchemaBuilder:
    """專門負責 Anki 筆記模型結構 (Schema) 的建置與管理。

    此類別屬於開發者工具 (Scripts Layer)，專門處理會破壞或覆蓋 Anki 資料庫結構的
    高危險操作。因為這些操作（如覆蓋樣板、增刪欄位）會導致本地資料庫與 AnkiWeb 產生
    Schema 不一致，從而觸發 'Sync status 2' (需要強制人工上傳覆蓋)，所以嚴格禁止
    被 Runtime 的 Service 匯入與使用。

    Builds and manages Anki note model schemas. This class belongs to
    the developer scripts layer and handles dangerous operations that
    can break or overwrite the Anki database schema (template
    overwrites, field add/remove). Such changes desync the local
    database from AnkiWeb and trigger a forced full upload, so this
    class must never be imported or used by runtime services.
    """

    def __init__(self, anki_client: AnkiClient):
        self._client = anki_client

    async def import_model_from_files(
        self,
        model_name: str,
        model_dir: str | Path,
    ) -> None:
        """從本地模板檔案匯入完整的筆記類型至 Anki。

        Import a complete note type into Anki from local template files.

        讀取定義檔 {model_name}.json（欄位 inOrderFields；可選的 cardTemplates 與 css），
        模板與 CSS 的檔名見 ``_read_card_templates``：沒有 cardTemplates 時為
        {model_name}_front.html、{model_name}_back.html、{model_name}_style.css。

        Args:
            model_name: 欲匯入的模型名（例如 'TOEIC_Coach_Dark'）。
                Model name to import (e.g. 'TOEIC_Coach_Dark').
            model_dir: 基礎模型資料夾路徑。Base directory of model assets.

        Raises:
            FileNotFoundError: 任一必要檔案丟失時。When any required file is missing.
            ValueError: JSON 檔案格式錯誤時。When the JSON definition file is invalid.
            AnkiConnectError: AnkiConnect 回傳錯誤時。When AnkiConnect returns an error.
        """
        base_path = Path(model_dir)

        json_path = base_path / f"{model_name}.json"
        if not json_path.is_file():
            logger.error("缺少必要匯入文件：%s", json_path)
            raise FileNotFoundError(f"找不到檔案: {json_path}")

        logger.info("正在讀取模型: %s", model_name)

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data: dict[str, object] = json.load(f)

                if "inOrderFields" not in data or not isinstance(data["inOrderFields"], list):
                    raise ValueError("JSON 定義檔內缺少 'inOrderFields' 陣列定義。")
                in_order_fields = [str(item) for item in data["inOrderFields"]]
                actual_model_name = str(data.get("modelName", model_name))

        except json.JSONDecodeError as decode_error:
            logger.error("解析 %s 時發生 JSON 錯誤。", json_path)
            raise ValueError(f"無效的 JSON 檔案: {decode_error}")

        card_templates, css_style = _read_card_templates(base_path, model_name, data)

        logger.info("檔案檢驗通過（%d 種卡片），準備向 AnkiConnect 提交模型建置請求...", len(card_templates))
        await self._client.create_model(
            model_name=actual_model_name,
            in_order_fields=in_order_fields,
            css=css_style,
            card_templates=card_templates,
            is_cloze=False,
        )

        logger.info("=== 模型 [%s] 成功匯入至 Anki！ ===", actual_model_name)

    async def update_model_templates_from_files(
        self,
        model_name: str,
        model_dir: str | Path,
    ) -> None:
        """從本地模板檔案更新現有筆記類型的 HTML 與 CSS。

        Update an existing note type's HTML templates and CSS from
        local files without overwriting field settings or resetting
        the model; structural field changes require confirmation.

        Args:
            model_name: 欲更新的模型名。Model name to update.
            model_dir: 基礎模型資料夾路徑。Base directory of model assets.

        Raises:
            FileNotFoundError: 任一必要模板檔案丟失時。When a required template file is missing.
            AnkiConnectError: AnkiConnect 回傳錯誤時。When AnkiConnect returns an error.
        """
        base_path = Path(model_dir)

        json_path = base_path / f"{model_name}.json"
        definition: dict[str, object] = {}
        if json_path.is_file():
            with open(json_path, "r", encoding="utf-8") as f:
                definition = json.load(f)
        # 先讀齊模板與 CSS：缺檔時在動到欄位結構之前就停下
        card_templates, css_style = _read_card_templates(base_path, model_name, definition)

        actual_model_name = model_name
        if json_path.is_file():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    actual_model_name = str(data.get("modelName", model_name))
                    
                    if "inOrderFields" in data:
                        target_fields = [str(item) for item in data["inOrderFields"]]
                        current_fields = await self._client.get_model_field_names(actual_model_name)
                        
                        fields_to_add = []
                        for field_name in target_fields:
                            if field_name not in current_fields:
                                fields_to_add.append(field_name)
                                
                        fields_to_reposition = []
                        add_details = []
                        repo_details = []
                        
                        temp_fields = current_fields.copy()
                        for i, field_name in enumerate(target_fields):
                            if field_name in fields_to_add:
                                temp_fields.insert(i, field_name)
                                add_details.append(f"{field_name} (插入至索引 {i})")
                            elif temp_fields.index(field_name) != i:
                                orig_idx = current_fields.index(field_name)
                                repo_details.append(f"{field_name} (索引 {orig_idx} -> {i})")
                                fields_to_reposition.append(field_name)
                                temp_fields.remove(field_name)
                                temp_fields.insert(i, field_name)
                                
                        if fields_to_add or fields_to_reposition:
                            print(f"\n⚠️ 警告: 偵測到模型 '{actual_model_name}' 的結構即將發生改變！")
                            if add_details:
                                print(f"  [+] 新增欄位: {', '.join(add_details)}")
                            if repo_details:
                                print(f"  [*] 調整順序: {', '.join(repo_details)}")
                            print("  (注意: 改變欄位結構將需要與 AnkiWeb 進行全量強制同步)")
                            
                            try:
                                confirm = input("是否允許這些結構性變更？ [y/N] ").strip().lower()
                            except EOFError:
                                # 非互動環境（CI/管線）拿不到確認，一律視為拒絕，
                                # 避免在無人把關下推送結構性變更。
                                print("🚫 非互動環境無法確認，視為拒絕，停止更新此模型。")
                                return
                            if confirm not in ('y', 'yes'):
                                print("🚫 已取消欄位結構變更，停止更新此模型。")
                                return
                        
                        # 1. 找出需要新增的欄位並新增
                        for i, field_name in enumerate(target_fields):
                            if field_name in fields_to_add:
                                await self.add_field_if_not_exists(actual_model_name, field_name, index=i)
                                
                        # 2. 確保欄位順序正確
                        current_fields = await self._client.get_model_field_names(actual_model_name)
                        for i, field_name in enumerate(target_fields):
                            if field_name in current_fields and current_fields.index(field_name) != i:
                                logger.info("正在調整欄位 '%s' 的順序至 %d...", field_name, i)
                                await self._client.reposition_model_field(actual_model_name, field_name, i)
                                current_fields = await self._client.get_model_field_names(actual_model_name)
            except Exception as e:
                # 欄位同步失敗時必須中止後續的模板/CSS 推送——若欄位
                # （如 Target_Language）沒建成功卻推上引用它的新模板，
                # 既有卡片會整批渲染錯誤（半套結構破壞）。
                logger.error(
                    "讀取或同步欄位時發生錯誤，已中止模型 '%s' 的模板更新: %s",
                    actual_model_name, e,
                )
                raise

        logger.info("正在讀取並更新模型樣板: %s", actual_model_name)

        logger.info("提交 HTML 樣板更新請求（%d 種卡片）...", len(card_templates))
        await self._client.update_model_templates(
            model_name=actual_model_name,
            templates={t["Name"]: {"Front": t["Front"], "Back": t["Back"]} for t in card_templates},
        )

        logger.info("提交 CSS 樣式更新請求...")
        await self._client.update_model_styling(
            model_name=actual_model_name,
            css=css_style
        )

        logger.info("=== 模型 [%s] 的模板與樣式已成功更新！ ===", actual_model_name)

    async def add_field_if_not_exists(self, model_name: str, field_name: str, index: int | None = None) -> None:
        """安全地新增欄位，若已存在則略過。

        Safely add a field to a model, skipping if it already exists.

        Args:
            model_name: 目標模型名。Target model name.
            field_name: 欲新增的欄位名。Field name to add.
            index: 插入位置索引；None 表示附加在最後。
                Insertion index; None appends at the end.
        """
        fields = await self._client.get_model_field_names(model_name)
        if field_name in fields:
            logger.info("欄位 '%s' 已存在於模型 '%s' 中，安全略過。", field_name, model_name)
            return

        logger.info("正在將欄位 '%s' 新增至模型 '%s'...", field_name, model_name)
        await self._client.add_model_field(model_name, field_name, index)
        logger.info("欄位新增成功！")

    async def remove_field_if_exists(self, model_name: str, field_name: str) -> None:
        """安全地刪除欄位，若不存在則略過。

        Safely remove a field from a model, skipping if it does not exist.

        Args:
            model_name: 目標模型名。Target model name.
            field_name: 欲刪除的欄位名。Field name to remove.
        """
        fields = await self._client.get_model_field_names(model_name)
        if field_name not in fields:
            logger.info("欄位 '%s' 不存在於模型 '%s' 中，無需刪除。", field_name, model_name)
            return

        logger.info("正在從模型 '%s' 刪除欄位 '%s'...", model_name, field_name)
        await self._client.remove_model_field(model_name, field_name)
        logger.info("欄位刪除成功！")

    async def rename_field_if_exists(self, model_name: str, old_field_name: str, new_field_name: str) -> None:
        """安全地重新命名欄位，若舊欄位不存在或新欄位已存在則略過。

        Safely rename a field, skipping when the old field is missing
        or the new field name already exists.

        Args:
            model_name: 目標模型名。Target model name.
            old_field_name: 原欄位名。Current field name.
            new_field_name: 新欄位名。New field name.
        """
        fields = await self._client.get_model_field_names(model_name)
        if old_field_name not in fields:
            logger.info("舊欄位 '%s' 不存在於模型 '%s' 中，無需重新命名。", old_field_name, model_name)
            return
        if new_field_name in fields:
            logger.info("新欄位 '%s' 已存在於模型 '%s' 中，無法重新命名。", new_field_name, model_name)
            return

        logger.info("正在將模型 '%s' 中的欄位 '%s' 重新命名為 '%s'...", model_name, old_field_name, new_field_name)
        await self._client.rename_model_field(model_name, old_field_name, new_field_name)
        logger.info("欄位重新命名成功！")
