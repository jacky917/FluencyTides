"""
基礎任務處理器 (Base Task Handler) 模組。

定義所有任務驅動處理器必須實作的 CRUD 介面。
每個具體 Handler 代表一種學習任務策略 (Strategy Pattern)。

Base task handler module.

Defines the CRUD interface every task-driven handler must implement.
Each concrete handler represents one learning-task strategy
(Strategy Pattern).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.card_service import CardService
    from app.services.relation_service import RelationService
    from app.infrastructure.llm.client import LLMClient


class BaseHandler(ABC):
    """卡片任務處理器的抽象基底類別。

    Abstract base class for card task handlers.

    每個 Handler 代表一種特定的學習任務 (例如: 語音教練、單字挖掘)。
    負責定義該任務所需的輸入 Schema、支援的模型，以及完整的 CRUD 生命週期。

    Each handler represents one specific learning task (e.g. speaking
    coach, vocabulary mining) and defines the task's input schema,
    supported models, and full CRUD lifecycle.

    設計決策：
    - Handler 與 Anki Model 是 N:M 的關係：一個 Handler 可操作多種 Model，
      一種 Model 也可被多個 Handler 使用。
    - 前端只需知道 handler_name 與對應的 input_schema，
      不需要理解 Anki 內部的欄位對應。

    Design decisions: handlers and Anki models are N:M; the frontend only
    needs handler_name and input_schema, never Anki field mappings.
    """

    @property
    @abstractmethod
    def handler_name(self) -> str:
        """暴露給前端呼叫的處理器名稱 (例如: 'speaking_coach')。

        Handler name exposed to the frontend (e.g. 'speaking_coach').
        """

    @property
    @abstractmethod
    def supported_models(self) -> list[str]:
        """這個處理器支援並能生成的 Anki 模型名稱清單。

        Anki model names this handler supports and can generate.
        """

    @abstractmethod
    def get_input_schema(self) -> dict[str, object]:
        """回傳 JSON Schema，定義前端呼叫 create 時需要提供哪些 parameters。

        Return the JSON schema describing the parameters required by the
        frontend when calling create.
        """

    # =========================================================================
    # CRUD 生命週期介面
    # =========================================================================

    @abstractmethod
    async def execute_create(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str,
        model_name: str,
        parameters: dict[str, object],
    ) -> int:
        """根據參數與 LLM 拼裝卡片欄位，並透過 CardService 建立 Anki 筆記。

        Assemble card fields from parameters (and the LLM) and create the
        Anki note via CardService.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            deck_name: 寫入目標的牌組名稱。Target deck name.
            model_name: 使用的 Anki 模型名稱 (必須在 supported_models 內)。
                Anki model name (must be in supported_models).
            parameters: 任務所需的輸入參數。Task input parameters.

        Returns:
            成功建立的 Note ID。ID of the created note.
        """

    @abstractmethod
    async def execute_read_list(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str | None = None,
    ) -> list[dict[str, object]]:
        """取得此任務處理器管轄的卡片列表，清洗隱藏欄位後回傳前端。

        List the cards governed by this handler, sanitizing hidden fields
        before returning them to the frontend.
        """

    @abstractmethod
    async def execute_read_graph(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        deck_name: str | None = None,
    ) -> dict[str, list[dict[str, object]]]:
        """取得此任務處理器專屬的知識圖譜資料。

        Get the knowledge-graph data specific to this handler.

        Returns:
            圖譜結構: {"nodes": [...], "links": [...]}；若該任務不支援
            圖譜，可拋出 NotImplementedError。Graph structure with "nodes"
            and "links"; may raise NotImplementedError if unsupported.
        """

    @abstractmethod
    async def execute_update(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        note_id: int,
        parameters: dict[str, object],
    ) -> None:
        """根據任務需求執行客製化的卡片更新邏輯 (如修改 JSON 陣列或更新特定欄位)。

        Execute task-specific update logic (e.g. modifying JSON arrays or
        updating particular fields).
        """

    @abstractmethod
    async def execute_delete(
        self,
        card_service: "CardService",
        relation_service: "RelationService",
        note_id: int,
    ) -> None:
        """刪除卡片。

        Delete a card.

        基本實作應包含：透過 CardService 刪除 Anki 卡片，並呼叫
        RelationService.delete_relations_for_note() 清理資料庫孤兒節點。
        A basic implementation should delete the Anki note via CardService
        and call RelationService.delete_relations_for_note() to clean up
        orphan nodes in the database.
        """
