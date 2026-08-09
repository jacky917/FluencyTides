"""
LLM 失敗紀錄模組 (LLM Failure Logger)

專門用於紀錄 LLM 在生成過程中發生業務邏輯失敗（例如結構不符、挖空定位失敗等）時的完整上下文。
將日誌儲存為 JSONL (JSON Lines) 格式，以便後續資料分析與 Prompt 優化。

Records the full context of business-logic failures that occur during LLM
generation (e.g. schema mismatches, cloze-position failures). Logs are stored
in JSONL (JSON Lines) format for later data analysis and prompt optimization.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# 確定日誌儲存的基礎目錄
LOG_DIR = Path(settings.LOG_FILE_PATH).parent


def log_llm_failure(
    task_name: str,
    model_name: str,
    prompt: str,
    raw_response: str | dict[str, Any],
    error_detail: str,
) -> None:
    """將 LLM 生成失敗的詳細資訊附加寫入 JSONL 日誌檔。

    Append detailed information about an LLM generation failure to a JSONL
    log file.

    Args:
        task_name: 任務名稱，例如 'JP_VerbPair_Cloze'。Task name, e.g.
            'JP_VerbPair_Cloze'.
        model_name: 負責生成的模型名稱，例如 'gemini-3.1-pro-preview'。
            Name of the generating model, e.g. 'gemini-3.1-pro-preview'.
        prompt: 餵給 LLM 的完整提示詞。The full prompt sent to the LLM.
        raw_response: LLM 吐出的原始文字或解析後的字典。The raw LLM output
            text or the parsed dict.
        error_detail: 導致失敗的具體原因描述。Description of the failure
            cause.
    """
    try:
        # 根據 task_name 動態決定檔案名稱
        # 例如 'JP_VerbPair_Cloze' -> 'JP_VerbPair_Cloze_failures.jsonl'
        safe_task_name = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in task_name)
        failure_log_path = LOG_DIR / f"{safe_task_name}_failures.jsonl"

        # 確保 logs 目錄存在
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        # 組合日誌物件
        log_entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "task": task_name,
            "model": model_name,
            "error_detail": error_detail,
            "prompt": prompt,
            "raw_response": raw_response,
        }

        # 以 JSON Lines 格式寫入 (Append)
        with open(failure_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        logger.debug("已將 LLM 失敗紀錄寫入: %s", failure_log_path)

    except Exception as e:
        # 寫入日誌失敗不應中斷主程式運作
        logger.error("寫入 LLM 失敗紀錄時發生錯誤: %s", e)
