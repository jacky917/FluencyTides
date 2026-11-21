"""Core Jinja2 template engine.

核心 Jinja2 模板引擎。

This module loads .j2 templates from the filesystem and renders them with
dynamic variables via the Jinja2 engine.

本模組負責從檔案系統載入 .j2 格式的模板，
並使用 Jinja2 渲染引擎將動態變數注入模板中。

Design decisions:
    - StrictUndefined mode is used so a missing variable fails immediately
      instead of silently rendering an empty string and producing an
      unexpected result.

設計決策：
    - 採用 StrictUndefined 模式，確保缺少變數時立即報錯，
      而非靜默輸出空字串導致非預期的渲染結果。
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
    """An error related to the template engine.

    模板引擎相關錯誤。
    """
    error_code = "TEMPLATE_ENGINE_ERROR"
    status_code = 500


class TemplateNotFoundError(TemplateEngineError):
    """The error raised when the specified template file cannot be found.

    找不到指定的模板檔案時拋出。
    """
    error_code = "TEMPLATE_NOT_FOUND"


class TemplateEngine:
    """The core engine for loading and rendering Jinja2 templates.

    Jinja2 模板載入與渲染核心引擎。

    Loads template files from a given directory and renders them with
    dynamic variables.

    負責從指定目錄載入模板檔案，並根據動態變數渲染內容。

    Attributes:
        _env: the Jinja2 Environment instance
            Jinja2 Environment 實例
        _template_dir: the base directory of the template files
            模板檔案所在基礎目錄路徑
    """

    def __init__(self, template_dir: str | Path) -> None:
        """Create an engine that loads templates from ``template_dir``.

        建立從 ``template_dir`` 載入模板的引擎。

        If the directory does not exist, it is created (with a warning).

        目錄不存在時會自動建立（並記錄警告）。

        Args:
            template_dir: the base directory containing the Jinja2 template
                files
                Jinja2 模板檔案所在的基礎目錄路徑
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
        """Load and render the specified Jinja2 template.

        載入並渲染指定的 Jinja2 模板。

        Args:
            template_path: the template path relative to the base directory
                (for example ``'prompts/audio_evaluator.j2'``)
                模板相對路徑（例如 ``'prompts/audio_evaluator.j2'``）
            **template_vars: the dynamic variables injected into the template
                注入模板的動態變數

        Returns:
            the fully rendered string
            渲染後的完整字串

        Raises:
            TemplateEngineError: if a variable is missing or another error
                occurs while rendering
                若渲染過程中發生變數缺失或其他錯誤
            TemplateNotFoundError: if the template file cannot be found
                若找不到對應的模板檔案
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
        """Check whether the specified template exists.

        檢查指定模板是否存在。

        Args:
            template_path: the template path relative to the base directory
                模板相對路徑

        Returns:
            ``True`` if the template file exists, otherwise ``False``
            模板檔案存在時為 ``True``，否則為 ``False``
        """
        target_path = self._template_dir / template_path
        return target_path.is_file()
