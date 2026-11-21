"""Familiarity (0–9) of trilingual speaking-card reference answers.

三語對話卡參考答案的熟識度（0–9）。

Each reference answer (one item of ``References_{ZH,JA,EN}``) may carry ``familiarity``: an integer 0–9, where
0 is most formal (formal occasions, people who matter) and 9 is most intimate and casual; omitted means
unrated. Plan: ``docs/archive/trilingual_familiarity_FEAT_2026-10-30.md``.

Validation rules (the same as ``readFamiliarity`` on the card back ``Speaking_Trilingual_Dark_back.html``;
``tests/test_trilingual_familiarity.py`` checks both sides against one table):

- Accepted: an integer 0–9 (including an integral float such as ``7.0``, which JSON read into JS cannot
  tell apart from 7), or a string of ASCII digits only (surrounding whitespace allowed).
- Unrated: the key missing, ``null``, an empty string, or whitespace only.
- Invalid: everything else (booleans, decimals, full-width or superscript digits, ``"7.0"``, arrays, values
  outside 0–9).

The write side (import) rejects invalid values; the read side (the card, recording evaluation) treats them as
unrated, because familiarity is only a tone hint for scoring and must not make a whole evaluation fail.

每句參考答案（``References_{ZH,JA,EN}`` 的一筆）可以帶 ``familiarity``：整數 0–9，0＝最正式（正式場合、
對重要的人），9＝最親密、最隨意；省略表示未標。計畫書：``docs/archive/trilingual_familiarity_FEAT_2026-10-30.md``。

判斷規則（後端與卡片背面 ``Speaking_Trilingual_Dark_back.html`` 的 ``readFamiliarity`` 相同，
``tests/test_trilingual_familiarity.py`` 以同一張表驗證兩邊）：

- 接受：0–9 的整數（含 ``7.0`` 這種整數值的浮點數，JSON 讀進 JS 後無法與 7 區分）、只含半形數字的字串
  （前後空白可）。
- 未標：沒有這個鍵、``null``、空字串或只有空白。
- 不合格：其他一切（布林、小數、全形或上標數字、``"7.0"``、陣列、超出 0–9）。

寫入端（匯入）遇到不合格的值報錯；讀取端（卡片、錄音評分）把不合格的值當成未標——熟識度只是給評分的
語氣參考，不該因為它讓整次評分失敗。
"""

from __future__ import annotations

import json
import re

#: The familiarity range.
#: 熟識度的範圍。
FAMILIARITY_MIN, FAMILIARITY_MAX = 0, 9

#: The reference answer fields of the three languages.
#: 三種語言的參考答案欄位。
REFERENCE_FIELDS = ("References_ZH", "References_JA", "References_EN")

# 只認半形數字：str.isdigit() 也接受全形、上標數字，int() 遇到上標還會拋出看不懂的錯誤
_ASCII_DIGITS = re.compile(r"\s*[0-9]+\s*")


def parse_familiarity(value: object) -> int | None:
    """Parse a familiarity value into an integer (rules: see the module docstring).

    熟識度轉成整數（規則見模組說明）。

    Args:
        value: the raw familiarity value
            熟識度的原始值

    Returns:
        the familiarity, or ``None`` when unrated
        熟識度；未標為 ``None``

    Raises:
        ValueError: if the value is invalid (the message always starts with 「熟識度」)
            若值不合格（訊息一律以「熟識度」開頭）
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        number = None
    elif isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and _ASCII_DIGITS.fullmatch(value):
        number = int(value)
    else:
        number = None
    if number is None:
        raise ValueError(f"熟識度必須是 0–9 的整數，不是 {value!r}")
    if not FAMILIARITY_MIN <= number <= FAMILIARITY_MAX:
        raise ValueError(f"熟識度必須在 {FAMILIARITY_MIN}–{FAMILIARITY_MAX} 之間，不是 {number}")
    return number


def familiarity_or_none(reference: object) -> int | None:
    """Read the familiarity of one reference answer.

    讀一句參考答案的熟識度。

    The read side is lenient; logging is left to the caller.

    讀取端寬鬆，記錄交給呼叫端。

    Args:
        reference: one reference answer; anything other than a dict counts as unrated
            一句參考答案；不是字典時視為未標

    Returns:
        the familiarity, or ``None`` when missing or invalid
        熟識度；沒填或不合格時為 ``None``
    """
    if not isinstance(reference, dict):
        return None
    try:
        return parse_familiarity(reference.get("familiarity"))
    except ValueError:
        return None


def normalize_reference_familiarity(fields: dict[str, object]) -> None:
    """Validate and normalize the familiarity in the three reference answer fields before import.

    匯入前檢查並正規化三個參考答案欄位的熟識度。

    ``fields`` is modified in place.

    就地修改 ``fields``。

    - A field value is replaced only when a familiarity needs changing (a digit string turned into an integer,
      an empty value removed); everything else stays as it is, because the text of reference answers
      (``<br>``, HTML entities, line breaks) must not be touched by this check.
    - A JSON-string field value is checked with plain JSON parsing (the import file is not an Anki field, so
      no HTML cleanup); when rewriting is needed it is replaced with a native list, which the import flow
      serializes later.
    - An omitted field (``None``) is skipped, as is a blank string.

    - 有熟識度要改（字串數字轉成整數、空值移除）時才換掉欄位值；其餘一律保持原樣——參考答案的內文
      （``<br>``、HTML 實體、換行）不能被這個檢查動到。
    - 欄位值是 JSON 字串時，以一般的 JSON 解析檢查（匯入檔不是 Anki 欄位，不做 HTML 清理）；
      需要改寫時換成原生陣列，之後由匯入流程序列化。
    - 欄位省略（``None``）或為空白字串時略過。

    Args:
        fields: the fields to import
            要匯入的欄位

    Raises:
        ValueError: if a field is not a JSON array, its JSON is malformed, or a sentence's familiarity is
            invalid; the message names the field and the sentence number
            若欄位不是 JSON 陣列、JSON 損毀，或某一句的熟識度不合格；訊息指出欄位與第幾句
    """
    for name in REFERENCE_FIELDS:
        value = fields.get(name)
        if value is None:
            continue
        if isinstance(value, str):
            if not value.strip():
                continue
            try:
                items = json.loads(value)
            except json.JSONDecodeError as e:
                raise ValueError(f"{name} 不是合法的 JSON：{e.msg}（第 {e.lineno} 行第 {e.colno} 欄）") from e
        else:
            items = value
        if not isinstance(items, list):
            raise ValueError(f"{name} 必須是 JSON 陣列，不是 {type(items).__name__}")

        changed = False
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict) or "familiarity" not in item:
                continue
            try:
                number = parse_familiarity(item["familiarity"])
            except ValueError as e:
                raise ValueError(f"{name} 第 {index} 句：{e}") from e
            if number is None:
                del item["familiarity"]
                changed = True
            elif item["familiarity"] != number or type(item["familiarity"]) is not int:
                item["familiarity"] = number
                changed = True
        if changed:
            fields[name] = items
