"""
Jinja2 Prompt 模板載入與渲染管理器。

本模組負責從檔案系統載入 .j2 格式的 Prompt 模板，
並使用 Jinja2 渲染引擎將動態變數注入模板中。

設計決策：
    - 使用 Jinja2 而非 Python 常數管理 Prompt，是因為：
      1. Prompt 模板可能包含複雜的條件分支與迴圈邏輯。
      2. 模板修改不需要改動 Python 代碼，降低維護成本。
      3. Jinja2 的模板繼承機制方便建立共用的 Prompt 片段。
    - 模板檔案以 {model_name}.j2 命名，與 anki_models/ 目錄的
      JSON 定義檔形成一對一對應關係。
    - 採用 StrictUndefined 模式，確保缺少變數時立即報錯，
      而非靜默輸出空字串。

職責分離：
    - 輸入面（Prompt 指令）：由 Jinja2 模板控制。
    - 輸出面（JSON Schema 約束）：由 anki_models/*.json 的 llm_schema 控制。
    - 兩者分離，避免 Prompt 中硬編碼 Schema 造成的維護災難。

Dependencies:
    - jinja2: 模板渲染引擎
"""

import logging
from pathlib import Path

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateNotFound,
)

from app.core.exceptions import PromptTemplateNotFoundError

logger = logging.getLogger(__name__)


class PromptManager:
    """Jinja2 Prompt 模板載入與渲染管理器。

    負責從指定目錄載入 .j2 模板檔案，並根據動態變數渲染
    最終的 System Prompt 字串，供 LLMClient 使用。

    模板命名慣例：
        - {model_name}.j2 → 對應 anki_models/{model_name}.json
        - 例如：TOEIC_Coach_Dark.j2 → TOEIC_Coach_Dark.json

    Attributes:
        _env: Jinja2 Environment 實例。
        _template_dir: 模板檔案所在目錄路徑。
    """

    def __init__(self, template_dir: str | Path) -> None:
        """初始化 PromptManager。

        Args:
            template_dir: Jinja2 模板檔案所在的目錄路徑。
        """
        self._template_dir = Path(template_dir)

        if not self._template_dir.exists():
            logger.warning(
                "Prompt 模板目錄不存在，將自動建立: %s",
                self._template_dir,
            )
            self._template_dir.mkdir(parents=True, exist_ok=True)

        # 使用 StrictUndefined 確保缺少變數時立即報錯，
        # 而非靜默輸出空字串導致 LLM 收到不完整的 Prompt。
        self._env = Environment(
            loader=FileSystemLoader(str(self._template_dir)),
            undefined=StrictUndefined,
            # 保留模板中的換行與空格，對 LLM Prompt 的格式至關重要
            keep_trailing_newline=True,
            trim_blocks=False,
            lstrip_blocks=False,
        )

        logger.info(
            "PromptManager 初始化完成，模板目錄: %s", self._template_dir
        )

    def render(
        self,
        model_name: str,
        **template_vars: str,
    ) -> str:
        """根據模型名稱載入並渲染對應的 Jinja2 模板。

        模板檔案命名慣例為 {model_name}.j2。
        動態變數透過 **template_vars 傳入模板。

        Args:
            model_name: 模型名稱，用於定位模板檔案。
                        例如 'TOEIC_Coach_Dark' → 載入 TOEIC_Coach_Dark.j2。
            **template_vars: 注入模板的動態變數。

        Returns:
            渲染後的完整 System Prompt 字串。

        Raises:
            PromptTemplateNotFoundError: 找不到對應的 .j2 模板檔案時。
        """
        template_filename = f"{model_name}.j2"

        try:
            template = self._env.get_template(template_filename)
        except TemplateNotFound:
            logger.error(
                "找不到 Prompt 模板: %s (目錄: %s)",
                template_filename,
                self._template_dir,
            )
            raise PromptTemplateNotFoundError(
                f"找不到模型 '{model_name}' 對應的 Prompt 模板 "
                f"'{template_filename}'。請確認 {self._template_dir} "
                f"目錄下存在此檔案。"
            )

        rendered = template.render(**template_vars)
        logger.debug(
            "Prompt 模板渲染完成: %s (長度: %d 字元)",
            template_filename,
            len(rendered),
        )
        return rendered

    def has_template(self, model_name: str) -> bool:
        """檢查指定模型是否存在對應的 Jinja2 模板。

        Args:
            model_name: 模型名稱。

        Returns:
            True 若模板檔案存在，否則 False。
        """
        template_path = self._template_dir / f"{model_name}.j2"
        return template_path.is_file()

    def list_templates(self) -> list[str]:
        """列出所有可用的 Prompt 模板名稱（不含 .j2 副檔名）。

        Returns:
            模板名稱字串列表。
        """
        templates: list[str] = []
        if self._template_dir.exists():
            for path in sorted(self._template_dir.glob("*.j2")):
                templates.append(path.stem)
        return templates
