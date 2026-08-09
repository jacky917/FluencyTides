"""
核心 Jinja2 模板引擎。

Core Jinja2 template engine.

本模組負責從檔案系統載入 .j2 格式的模板，
並使用 Jinja2 渲染引擎將動態變數注入模板中。

This module loads .j2 templates from the filesystem and renders them with
dynamic variables via the Jinja2 engine.

設計決策：
    - 採用 StrictUndefined 模式，確保缺少變數時立即報錯，
      而非靜默輸出空字串導致非預期的渲染結果。

Design decisions:
    - StrictUndefined mode is used so a missing variable fails immediately
      instead of silently rendering an empty string.
"""

import logging
from pathlib import Path

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateNotFound,
    TemplateError,
)

from app.core.exceptions import FluencyTidesError

logger = logging.getLogger(__name__)


class TemplateEngineError(FluencyTidesError):
    """模板引擎相關錯誤。

    Template-engine related error.
    """
    error_code = "TEMPLATE_ENGINE_ERROR"
    status_code = 500


class TemplateNotFoundError(TemplateEngineError):
    """找不到指定的模板檔案時拋出。

    Raised when the specified template file cannot be found.
    """
    error_code = "TEMPLATE_NOT_FOUND"


class TemplateEngine:
    """Jinja2 模板載入與渲染核心引擎。

    Core engine for loading and rendering Jinja2 templates.

    負責從指定目錄載入模板檔案，並根據動態變數渲染內容。

    Loads template files from a given directory and renders them with
    dynamic variables.

    Attributes:
        _env: Jinja2 Environment 實例。Jinja2 Environment instance.
        _template_dir: 模板檔案所在基礎目錄路徑。Base directory of templates.
    """

    def __init__(self, template_dir: str | Path) -> None:
        """初始化 TemplateEngine。

        Initialize the TemplateEngine.

        Args:
            template_dir: Jinja2 模板檔案所在的基礎目錄路徑。
                Base directory containing the Jinja2 template files.
        """
        self._template_dir = Path(template_dir)

        if not self._template_dir.exists():
            logger.warning(
                "模板目錄不存在，將自動建立: %s",
                self._template_dir,
            )
            self._template_dir.mkdir(parents=True, exist_ok=True)

        # 使用 StrictUndefined 確保缺少變數時立即報錯
        self._env = Environment(
            loader=FileSystemLoader(str(self._template_dir)),
            undefined=StrictUndefined,
            # 保留模板中的換行與空格，對 LLM Prompt 的格式至關重要
            keep_trailing_newline=True,
            trim_blocks=False,
            lstrip_blocks=False,
        )

        logger.info(
            "TemplateEngine 初始化完成，基礎模板目錄: %s", self._template_dir
        )

    def render(
        self,
        template_path: str,
        **template_vars: object,
    ) -> str:
        """載入並渲染指定的 Jinja2 模板。

        Load and render the specified Jinja2 template.

        Args:
            template_path: 模板相對路徑 (例如 'prompts/audio_evaluator.j2')。
                Template path relative to the base directory.
            **template_vars: 注入模板的動態變數。Dynamic variables injected
                into the template.

        Returns:
            渲染後的完整字串。The fully rendered string.

        Raises:
            TemplateNotFoundError: 找不到對應的模板檔案時。
                Raised when the template file cannot be found.
            TemplateEngineError: 渲染過程中發生變數缺失或其他錯誤時。
                Raised on missing variables or other rendering errors.
        """
        try:
            template = self._env.get_template(template_path)
        except TemplateNotFound:
            logger.error(
                "找不到模板: %s (目錄: %s)",
                template_path,
                self._template_dir,
            )
            raise TemplateNotFoundError(
                f"找不到模板 '{template_path}'。請確認 {self._template_dir} "
                f"目錄下存在此檔案。"
            )

        try:
            rendered = template.render(**template_vars)
            logger.debug(
                "模板渲染完成: %s (長度: %d 字元)",
                template_path,
                len(rendered),
            )
            return rendered
        except TemplateError as e:
            logger.error("模板 %s 渲染失敗: %s", template_path, e)
            raise TemplateEngineError(f"模板 {template_path} 渲染失敗: {e}") from e

    def has_template(self, template_path: str) -> bool:
        """檢查指定模板是否存在。

        Check whether the specified template exists.

        Args:
            template_path: 模板相對路徑。Template path relative to base dir.

        Returns:
            True 若模板檔案存在，否則 False。True if the template file
            exists, otherwise False.
        """
        target_path = self._template_dir / template_path
        return target_path.is_file()
