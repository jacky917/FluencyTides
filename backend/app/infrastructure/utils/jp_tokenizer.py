"""Explicit dictionary resolution for the Japanese tokenizer; never let fugashi pick one implicitly.

日文分詞器的詞典解析（明確指定，不讓 fugashi 自己挑）。

Why this module exists: called without arguments, ``fugashi.Tagger()`` detects a dictionary at run time on its
own: it uses the full ``unidic`` when available, and otherwise **silently** falls back to ``unidic-lite``. The two
have identical APIs and the fallback prints nothing, so "which dictionary" becomes a function of the runtime
environment: the same code produced different tokenizations on the development machine (full) and in the
container (lite only).

The measured cost on 2026-09-04: 490 core-verb sub-cards produced in a container all passed lite's validation, but
6 of them failed under the full dictionary. Every divergence fell on the boundary of nominalized continuative forms
(in ``先輩の答え``, lite tags ``答え`` as a verb, the full dictionary as a noun), and at least one was an actual bad
card. Worse, all dry-run baselines had been measured on the full dictionary, so they did not match the production
environment and the numbers lost their meaning.

This module therefore does three things:
    1. **Specify** the dictionary path explicitly (``-d``); the resolution order lives in code, not in detection.
    2. **Fall back loudly**: when the full dictionary is missing, log a WARNING instead of staying silent.
    3. **Make it queryable**: the resolved result is exposed through the ``/api/v1/jp/tokenizer/dictionary``
       endpoint and each script's startup log, so environment differences are visible at a glance.

Why the full dictionary is preferred: unidic 3.1.0 is newer and larger than unidic-lite's older 2.1.2 build, and
judges boundaries such as nominalized continuative forms and same-surface different readings more accurately. lite
is kept as the fallback so containers and CI need not download a 1 GB dictionary just to run (see the dual-track
dictionary note in requirements.txt).

為什麼需要這個模組：``fugashi.Tagger()`` 不帶參數時由 fugashi 在執行當下
自行偵測詞典——完整版 ``unidic`` 可用就用它，否則**靜默**退回
``unidic-lite``。兩者 API 完全相同、退回時沒有任何訊息，於是「用哪本」變成
執行環境狀態的函數：同一份程式碼在開發機（完整版）與容器（只有 lite）跑出
不同的分詞結果。

2026-09-04 實測代價：某次在容器內產出的 490 張核心動詞子卡，全部通過
lite 的驗證，但其中 6 張在完整版下不通過——分歧全落在「連用形名詞化」這個
邊界（``先輩の答え`` 的 ``答え`` lite 判動詞、完整版判名詞），其中至少一張
是實際錯卡。更麻煩的是所有 dry-run 基線都在完整版上量的，與正式跑的環境對
不上，數字失去意義。

因此本模組做三件事：
    1. **明確指定**詞典路徑（``-d``），解析順序寫在程式碼裡而非交給偵測。
    2. **退回時大聲說**——找不到完整版時記 WARNING，不再靜默。
    3. **可查詢**——解析結果由 ``/api/v1/jp/tokenizer/dictionary`` 端點與
       各腳本的啟動 log 對外揭露，環境差異一眼可見。

偏好完整版的理由：unidic 3.1.0 比 unidic-lite 的 2.1.2 舊 build 新且大，
連用形名詞化、同表層異讀等邊界判得較準。仍保留 lite 作為回退，容器與 CI
不必為了跑起來先下載 1GB 詞典（見 requirements.txt 的詞典雙軌說明）。
"""

import importlib
import logging
import os
from dataclasses import dataclass
from functools import lru_cache

import fugashi

logger = logging.getLogger(__name__)

KIND_UNIDIC = "unidic"
KIND_UNIDIC_LITE = "unidic-lite"

# 解析順序：(套件模組名, 對外 kind)。完整版優先，lite 回退。
# Resolution order: full unidic first, unidic-lite as the fallback.
_CANDIDATES = ((KIND_UNIDIC, "unidic"), (KIND_UNIDIC_LITE, "unidic_lite"))


@dataclass(frozen=True)
class TokenizerDictionary:
    """The tokenizer dictionary actually loaded by this process.

    本行程實際載入的分詞詞典。

    Attributes:
        kind: ``unidic`` (the full dictionary) or ``unidic-lite``
            ``unidic``（完整版）或 ``unidic-lite``
        version: the version string shipped inside the dictionary, for example ``unidic-3.1.0+2021-08-31``
            詞典自帶的版本字串，如 ``unidic-3.1.0+2021-08-31``
        dicdir: the absolute path to the dictionary directory
            詞典目錄的絕對路徑
        is_preferred: whether this is the preferred full dictionary; ``False`` means running on the fallback
            是否為偏好的完整版；``False`` 代表跑在回退詞典上
    """

    kind: str
    version: str
    dicdir: str
    is_preferred: bool


def _read_version(dicdir: str) -> str:
    """Read the ``version`` file in the dictionary directory.

    讀詞典目錄裡的 ``version`` 檔。

    Args:
        dicdir: the dictionary directory
            詞典目錄

    Returns:
        the version string, or ``"unknown"`` if the file cannot be read or is empty
        版本字串；讀不到或為空時為 ``"unknown"``
    """
    path = os.path.join(dicdir, "version")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or "unknown"
    except OSError:
        return "unknown"


def _probe(kind: str, module_name: str) -> TokenizerDictionary | None:
    """Check whether one dictionary package is actually usable.

    檢查某個詞典套件是否真的可用。

    An installed package does not mean the dictionary is present: the full ``unidic`` needs a separate
    ``python -m unidic download``, and until then ``DICDIR`` is an empty directory. Checking whether ``sys.dic``
    exists is more reliable than a successful import.

    套件裝了不等於詞典在：完整版 ``unidic`` 需要另跑 ``python -m unidic
    download``，沒下載時 ``DICDIR`` 會是空目錄。以 ``sys.dic`` 是否存在
    判定，比 import 成功可靠。

    Args:
        kind: the public kind name
            對外的 kind 名稱
        module_name: the package module to import
            要 import 的套件模組名

    Returns:
        a description of the dictionary if usable, otherwise ``None``
        可用時回傳描述，否則 ``None``
    """
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    dicdir = getattr(module, "DICDIR", "") or ""
    if not dicdir or not os.path.isfile(os.path.join(dicdir, "sys.dic")):
        return None
    return TokenizerDictionary(
        kind=kind,
        version=_read_version(dicdir),
        dicdir=dicdir,
        is_preferred=(kind == KIND_UNIDIC),
    )


@lru_cache(maxsize=1)
def resolve_dictionary() -> TokenizerDictionary:
    """Resolve which dictionary this process uses; cached, so the log line appears only once per process.

    決定本行程要用哪本詞典（結果快取，log 每行程只出現一次）。

    Returns:
        the resolved dictionary
        解析結果

    Raises:
        RuntimeError: if neither dictionary is usable. Tokenization is the foundation of sentence-selection
            validation, so the pipeline must not continue with wrong tokenization.
            若兩本詞典都不可用。分詞是選句驗證的地基，此時不該讓流程帶著錯誤的分詞繼續跑。
    """
    for kind, module_name in _CANDIDATES:
        found = _probe(kind, module_name)
        if found is None:
            continue
        if found.is_preferred:
            logger.info("🔤 分詞詞典：%s %s（%s）", found.kind, found.version, found.dicdir)
        else:
            logger.warning(
                "⚠️ 分詞詞典回退至 %s %s（%s）——完整版 unidic 不可用。"
                "此詞典對連用形名詞化等邊界的判定與完整版不同，"
                "產出結果無法與完整版環境的基線相比；"
                "請在此環境執行 `python -m unidic download`。",
                found.kind, found.version, found.dicdir,
            )
        return found
    raise RuntimeError(
        "找不到任何可用的日文分詞詞典（unidic / unidic-lite 皆缺）。"
        "請安裝 unidic-lite，或安裝 unidic 後執行 `python -m unidic download`。"
    )


def create_tagger() -> fugashi.Tagger:
    """Create a Tagger bound to the resolved dictionary.

    建立指定詞典的 Tagger。

    Specifies the path explicitly with ``-d`` instead of relying on fugashi's auto-detection. Each call returns a
    new instance (the caller holds it; the derivation cache in ``candidate_validator`` is keyed by
    ``id(tagger)``, so sharing it or not is equally safe).

    以 ``-d`` 明確指定路徑，不依賴 fugashi 的自動偵測。每次呼叫回傳新實例
    （呼叫端自行持有；``candidate_validator`` 的導出快取以 ``id(tagger)``
    分隔，共用與否都安全）。

    Returns:
        a Tagger bound to the resolved dictionary
        綁定解析結果的 Tagger

    Raises:
        RuntimeError: if neither dictionary is usable (see ``resolve_dictionary``)
            若兩本詞典都不可用（見 ``resolve_dictionary``）
    """
    dictionary = resolve_dictionary()
    return fugashi.Tagger(f'-d "{dictionary.dicdir}"')
