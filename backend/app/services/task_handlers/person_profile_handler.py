"""
人脈卡任務處理器 (Person Profile Handler) 模組。

負責 Person_Profile_Dark：網頁與 TG 建立、修改一般欄位、刪除。JSON 清單與照片不能直接以文字寫入，
之後經由具名動作與 JSON 欄位編輯寫入（計畫書 ``docs/wip/person_profile_card_FEAT_2026-10-23.md``
§3.11、§3.12）。

Person profile handler module: owns Person_Profile_Dark; JSON lists and the
photo are written through actions, not as plain text.
"""

import secrets
from datetime import date, datetime
from typing import override

from app.core.exceptions import FieldNotEditableError
from app.schemas.person_profile import PERSON_JSON_ITEMS, PERSON_NOTE_TYPE, PersonCreateParams
from app.services.card_service import CardService
from app.services.relation_service import RelationService
from app.services.task_handlers.base import BaseHandler, field_text, note_field_values
from app.services.task_handlers.capabilities import CreateCapability, HandlerCapabilities, Operation
from app.services.task_handlers.registry import register_handler

#: 系統維護、不能經 ``update_fields`` 改的欄位：TG 深連結以 ``Card_ID`` 找人，改了就斷線。
#: Fields the system owns; TG deep links find a person by ``Card_ID``.
SYSTEM_FIELDS = ("Card_ID",)


def today_iso() -> str:
    """今天（伺服器時區；正式機容器設 ``TZ=Asia/Tokyo``）。Today in the server's time zone."""
    return date.today().isoformat()


def new_card_id() -> str:
    """新的 ``Card_ID``：``PP_YYYYMMDD_HHMMSS_xxxx``（同一秒建立多人也不重複）。

    A new ``Card_ID``; the random suffix keeps same-second creations apart.
    只含英數字與底線，能放進 TG 深連結（``{動作}-{欄位}-{序號}-{Card_ID}``）。
    """
    return f"PP_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"


@register_handler
class PersonProfileHandler(BaseHandler):
    """人脈卡任務處理器。

    Person profile card task handler.

    - 建立（網頁、TG）：參數是 ``PersonCreateParams``；自動產生 ``Card_ID``、寫入 ``TG_Bot`` 與
      ``Updated_At``、六個 JSON 欄位填 ``[]``。允許稱呼重複（不同的人可以都叫「田中さん」）。
    - 修改一般欄位：JSON、照片與 ``Card_ID`` 不能直接改；有改動時更新 ``Updated_At``。
    - 刪除：標準刪除。
    """

    CAPABILITIES = HandlerCapabilities(
        create=CreateCapability(
            supported=True, channels=("web", "tg"), flow="form", deck="selected", note_type=PERSON_NOTE_TYPE
        ),
        update_fields=Operation(supported=True),
        delete=Operation(supported=True),
    )
    JSON_FIELDS = tuple(PERSON_JSON_ITEMS)
    MEDIA_FIELDS = ("Photo",)
    CREATE_TAGS = ("PersonProfile", "HandlerGenerated")
    CREATE_PARAMS = PersonCreateParams

    @property
    @override
    def handler_name(self) -> str:
        """處理器名稱。Handler name."""
        return "person_profile"

    @property
    @override
    def title(self) -> str:
        """中文名稱。Display title."""
        return "人脈卡"

    @property
    @override
    def supported_models(self) -> list[str]:
        """支援的 Anki 模型名稱清單。Supported Anki model names."""
        return [PERSON_NOTE_TYPE]

    @override
    async def execute_create(
        self,
        card_service: CardService,
        relation_service: RelationService,
        deck_name: str,
        model_name: str,
        parameters: dict[str, object],
    ) -> list[int]:
        """建立一個人：填入使用者給的欄位與系統欄位。

        Create a person with the given fields and the system fields.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務（不用）。Relation service (unused).
            deck_name: 目標牌組。Target deck.
            model_name: 筆記類型（服務層已確認）。The note type.
            parameters: ``PersonCreateParams`` 驗證過的值，加上服務層補的 ``tg_bot``。
                The validated parameters plus ``tg_bot``.

        Returns:
            建立的 Note ID。The created note ID.
        """
        values = dict(parameters)
        tg_bot = str(values.pop("tg_bot", "") or "")
        fields = {name: str(value) for name, value in values.items() if value not in (None, "")}
        fields.update({name: "[]" for name in self.JSON_FIELDS})
        fields.update({"Card_ID": new_card_id(), "TG_Bot": tg_bot, "Updated_At": today_iso()})
        note_id = await card_service.create_note(
            deck_name=deck_name,
            model_name=model_name,
            fields=fields,
            tags=list(self.CREATE_TAGS),
            allow_duplicate=True,
        )
        return [note_id]

    @override
    async def execute_update(
        self,
        card_service: CardService,
        relation_service: RelationService,
        note_id: int,
        parameters: dict[str, object],
    ) -> None:
        """修改一般欄位（標準實作），``Card_ID`` 不能改；有改動時更新 ``Updated_At``。

        Update plain fields; ``Card_ID`` is locked; ``Updated_At`` follows changes.

        Raises:
            FieldNotEditableError: 改了 JSON、照片或 ``Card_ID``。When a locked field is given.
            其他：同 ``BaseHandler.execute_update``。Otherwise as the base.
        """
        fields = parameters.get("fields")
        changed = False
        if parameters.get("action") == "update_fields" and isinstance(fields, dict):
            locked = [name for name in fields if name in SYSTEM_FIELDS]
            if locked:
                raise FieldNotEditableError(f"這些欄位由系統維護，不能直接修改：{'、'.join(locked)}")
            current = note_field_values(await card_service.get_note(note_id))
            changed = any(field_text(value) != current.get(name) for name, value in fields.items())
        await super().execute_update(card_service, relation_service, note_id, parameters)
        if changed and "Updated_At" not in fields:
            await self._touch(card_service, note_id)

    async def _touch(self, card_service: CardService, note_id: int) -> None:
        """寫入今天的 ``Updated_At``。Stamp today's date into ``Updated_At``."""
        await card_service.update_note_fields(note_id, {"Updated_At": today_iso()}, model_name=PERSON_NOTE_TYPE)
