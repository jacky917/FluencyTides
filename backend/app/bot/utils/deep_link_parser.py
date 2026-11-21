"""Telegram deep link parser module.

Telegram Deep Link 解析器模組。

Parses the payload string of ``/start <payload>`` into the corresponding ``DeepLinkAction`` Pydantic
model. This follows Clean Architecture so that the controller layer never has to parse string formats
by itself.

負責將 ``/start <payload>`` 的 payload 字串解析為對應的 ``DeepLinkAction`` Pydantic Model。
這實踐了 Clean Architecture，確保 Controller 層不需要自己解析字串格式。
"""

import logging
from typing import get_args

from app.schemas.tg.deep_link import (
    DeepLinkAction,
    DeleteEntryAction,
    GenerateCardAction,
    PersonLinkAction,
    RecordAudioAction,
    AddAudioAction,
    TTSAudioAction,
)

logger = logging.getLogger(__name__)

#: Action prefixes of person-card deep links mapped to ``PersonLinkAction.verb``.
#: 人脈卡深連結的動作前綴 → ``PersonLinkAction.verb``。
PERSON_VERBS = {"pview": "view", "padd": "add", "pdel": "del", "pdone": "done", "pend": "end", "psec": "section",
                "pedit": "edit", "pphoto": "photo", "ptopic": "topic", "pmask": "mask", "pitm": "item", "pfld": "field"}
# 與 PersonLinkAction.verb 的 Literal 是兩份清單：漏加時 import 就失敗，不要等到解析深連結才發現
assert set(PERSON_VERBS.values()) == set(get_args(PersonLinkAction.model_fields["verb"].annotation)), \
    "PERSON_VERBS 與 PersonLinkAction.verb 不一致"


class DeepLinkParser:
    """A parser that turns deep link payloads into ``DeepLinkAction`` models.

    把 Deep Link payload 轉成 ``DeepLinkAction`` model 的解析器。
    """

    @staticmethod
    def parse(payload: str) -> DeepLinkAction | None:
        """Parse a deep link payload.

        解析 Deep Link Payload。

        Supported format: ``{Action}-{FieldName}-{Index}-{Card_ID}``

        - Action: ``rec`` (or ``recording``), ``del``, ``addaudio``, ``ttsaudio``; for person cards ``pview``,
          ``padd``, ``pdel``, ``pdone``, ``pend``, ``psec``, ``pedit``, ``pphoto``, ``ptopic``, ``pmask``,
          ``pitm``, ``pfld``
        - FieldName: the Anki field name (for example ``Recordings``); for person cards see
          ``PersonLinkAction``
        - Index: a number or ``last``; ``none`` when not applicable
        - Card_ID: the card's unique identifier

        支援的格式：``{Action}-{FieldName}-{Index}-{Card_ID}``

        - Action：``rec``（或 ``recording``）、``del``、``addaudio``、``ttsaudio``；人脈卡為 ``pview``、``padd``、
          ``pdel``、``pdone``、``pend``、``psec``、``pedit``、``pphoto``、``ptopic``、``pmask``、``pitm``、``pfld``
        - FieldName：對應 Anki 的欄位名稱（例如 ``Recordings``）；人脈卡的格式見 ``PersonLinkAction``
        - Index：數字或 ``last``，如果不適用則為 ``none``
        - Card_ID：卡片的唯一識別碼

        The legacy non-standard ``gen_{target_id}`` form is also accepted and becomes a
        ``GenerateCardAction``.

        另外也接受舊版非標準的 ``gen_{target_id}``，解析為 ``GenerateCardAction``。

        Args:
            payload: the string after ``/start``
                ``/start`` 之後的字串

        Returns:
            the parsed ``DeepLinkAction`` subclass, or ``None`` if the payload is empty, the format is
            invalid, a field fails validation, or the action prefix is unknown
            解析出的 ``DeepLinkAction`` 子類別；payload 為空、格式無效、欄位驗證失敗或動作前綴未知時為 ``None``
        """
        if not payload:
            return None

        # 針對 gen_{target_id} 這種舊版非標準指令，先做特例處理
        if payload.startswith("gen_"):
            return GenerateCardAction(target_id=payload[4:])

        parts = payload.split("-", 3)
        if len(parts) != 4:
            logger.warning("解析 Deep Link 失敗: 格式不符合 4 段式要求 (payload=%s)", payload)
            return None

        action, field_name, index, card_id = parts

        try:
            if action in ("rec", "recording"):
                return RecordAudioAction(
                    field_name=field_name, index=index, card_id=card_id
                )
            elif action == "del":
                return DeleteEntryAction(
                    field_name=field_name, index=index, card_id=card_id
                )
            elif action == "addaudio":
                return AddAudioAction(
                    field_name=field_name, index=index, card_id=card_id
                )
            elif action == "ttsaudio":
                return TTSAudioAction(
                    field_name=field_name, index=index, card_id=card_id
                )
            elif action in PERSON_VERBS:
                return PersonLinkAction(
                    verb=PERSON_VERBS[action], field_name=field_name, index=index, card_id=card_id
                )
        except (ValueError, TypeError) as e:
            logger.warning("解析 Deep Link 失敗: payload=%s, error=%s", payload, e)
            return None

        # 未知的 prefix
        logger.warning("未知的 Deep Link Action: %s", action)
        return None
