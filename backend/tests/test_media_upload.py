"""網頁上傳媒體的測試（media_upload 計畫書 §4）：檔名、格式判斷、上傳服務、暫存區、存進 Anki 與撤回、``/media``。

Tests for web media uploads: names, format sniffing, the upload service and
``POST /api/v1/media``.

API 的部分用真實的 app（不進入 lifespan），只把 Anki 換成假的，轉檔器換成不呼叫 ffmpeg 的替身；
最後一組在有 ffmpeg 的機器上用真的 ffmpeg 轉一次檔（沒有就略過）。
"""

import asyncio
import base64
import io
import re
import shutil
import time
import wave
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.exceptions import (
    DuplicateCardError,
    MediaConverterUnavailableError,
    MediaConvertFailedError,
    MediaFieldInvalidError,
    MediaFileNotFoundError,
    MediaUploadExpiredError,
    MediaTooLargeError,
    MediaTypeUnsupportedError,
)
from app.infrastructure.anki.client import AnkiConnectError
from app.infrastructure.ffmpeg.client import FfmpegClient, FfmpegError, FfmpegTimeoutError
from app.schemas.anki import AnkiStoreMediaParams
from app.services import media_names, media_service
from app.services.media_commit import committed_media, staged_names
from app.services.media_names import (
    MEDIA_KINDS,
    STORED_EXTENSIONS,
    media_field_text,
    new_media_name,
    uploaded_media_kind,
    wrap_media,
)
from app.services.media_service import (
    ACCEPTED_FORMATS,
    CONVERT_TIMEOUT_SECONDS,
    CONVERT_TO_MP3,
    MAX_MEDIA_BYTES,
    MediaService,
    sniff_format,
)
from app.services.media_staging import STAGING_TTL_SECONDS, MediaStaging, media_staging

NAME_PATTERN = re.compile(r"web_(audio|image)_\d{8}_\d{6}_[0-9a-f]{6}\.[a-z0-9]+")

# 各格式開頭的特徵，後面補一些內容
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
OGG = b"OggS\x00\x02" + b"\x00" * 32
WEBM = b"\x1a\x45\xdf\xa3\x9fB\x86\x81\x01" + b"\x00" * 32
M4A = b"\x00\x00\x00\x1cftypM4A \x00\x00\x00\x00" + b"\x00" * 32
MP3_ID3 = b"ID3\x04\x00\x00\x00\x00\x00\x21" + b"\x00" * 32
MP3_FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 32  # MPEG-1 Layer III、128 kbps、44.1 kHz
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00" + b"\x00" * 32
AAC_ADTS = b"\xff\xf1\x50\x80" + b"\x00" * 32  # 同步字和 mp3 一樣，但層是 00


def _run(coro):
    return asyncio.run(coro)


class MediaAnki:
    """只會存媒體檔的假 Anki。"""

    def __init__(self, error: Exception | None = None, rename: str | None = None) -> None:
        self.stored: list[AnkiStoreMediaParams] = []
        self.error = error
        self.rename = rename

    async def store_media_file(self, params: AnkiStoreMediaParams) -> str:
        if self.error:
            raise self.error
        self.stored.append(params)
        return self.rename or params.filename

    def content(self, index: int = 0) -> bytes:
        return base64.b64decode(self.stored[index].data or "")


class FakeConverter:
    """不呼叫 ffmpeg 的轉檔器：記下來源檔的內容、路徑與逾時，回傳固定的 mp3。"""

    def __init__(self, result: bytes = MP3_ID3, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.sources: list[tuple[str, bytes]] = []
        self.paths: list[Path] = []
        self.timeouts: list[float | None] = []

    async def convert_file_to_mp3(self, source: Path, timeout: float | None = None) -> bytes:
        self.sources.append((source.name, source.read_bytes()))
        self.paths.append(source)
        self.timeouts.append(timeout)
        if self.error:
            raise self.error
        return self.result


def _broken(message: str = "moov atom not found") -> FfmpegError:
    """ffmpeg 跑了、但以非零碼結束（檔案本身的問題）。"""
    return FfmpegError(message, return_code=1, stderr_output=message)


def _service(staging: MediaStaging, converter: FakeConverter | None = None) -> MediaService:
    return MediaService(staging, converter_factory=lambda: converter or FakeConverter())  # type: ignore[arg-type]


# =============================================================================
# 檔名與媒體欄位的值
# =============================================================================


class TestNames:
    def test_new_name_format(self):
        name = new_media_name("audio", "mp3")
        assert re.fullmatch(r"web_audio_\d{8}_\d{6}_[0-9a-f]{6}\.mp3", name)
        assert uploaded_media_kind(name) == "audio"
        assert uploaded_media_kind(new_media_name("image", "webp")) == "image"

    def test_random_part_tells_names_apart_within_a_second(self):
        """時間只到秒：同一秒的兩次上傳靠 6 碼隨機碼區分（固定隨機碼，測試不會偶發失敗）。"""
        with mock.patch.object(media_names.secrets, "token_hex", side_effect=["0a0a0a", "0b0b0b"]) as token:
            first, second = new_media_name("audio", "mp3"), new_media_name("audio", "mp3")
        assert first.endswith("_0a0a0a.mp3") and second.endswith("_0b0b0b.mp3")
        token.assert_called_with(3)

    def test_format_tables_agree(self):
        """存進 Anki 的副檔名 = 接受的格式扣掉要轉檔的、加上 mp3；不一致時上傳會成功、填欄位卻被拒。"""
        assert set(MEDIA_KINDS) == set(ACCEPTED_FORMATS) == set(STORED_EXTENSIONS) == set(MAX_MEDIA_BYTES)
        for kind in MEDIA_KINDS:
            accepted = set(ACCEPTED_FORMATS[kind])
            converted = accepted & CONVERT_TO_MP3
            assert set(STORED_EXTENSIONS[kind]) == (accepted - CONVERT_TO_MP3) | ({"mp3"} if converted else set())
        assert CONVERT_TO_MP3 <= set(ACCEPTED_FORMATS["audio"])

    @pytest.mark.parametrize(
        "name",
        [
            "a.mp3",
            "rec_SC_1_20261020_153012.ogg",  # TG 錄音的檔名
            "avatar_1_20261020_153012.jpg",  # TG 頭像的檔名
            "web_audio_20261020_153012_3f9a1c.exe",
            "web_audio_20261020_153012_3f9a1c.jpg",  # 種類和副檔名不一致
            "web_image_20261020_153012_3f9a1c.mp3",
            "web_audio_20261020_153012_3F9A1C.mp3",  # 隨機碼是小寫
            "web_audio_20261020_153012_3f9a1c.mp3 ",
            "../web_audio_20261020_153012_3f9a1c.mp3",
            "web_video_20261020_153012_3f9a1c.mp4",
            "web_audio_２０２６１０２０_１５３０１２_3f9a1c.mp3",  # 全形數字
            "",
        ],
    )
    def test_other_names_are_not_uploads(self, name):
        assert uploaded_media_kind(name) is None

    def test_wrap(self):
        assert wrap_media("audio", "a.mp3") == "[sound:a.mp3]"
        assert wrap_media("image", "a.jpg") == '<img src="a.jpg">'


class TestMediaFieldText:
    AUDIO = "web_audio_20261020_153012_3f9a1c.mp3"
    IMAGE = "web_image_20261020_153012_3f9a1c.png"

    def test_uploaded_name_is_wrapped(self):
        assert media_field_text("Word_Audio", "audio", self.AUDIO) == f"[sound:{self.AUDIO}]"
        assert media_field_text("Photo", "image", f"  {self.IMAGE} ") == f'<img src="{self.IMAGE}">'

    def test_empty_clears(self):
        assert media_field_text("Word_Audio", "audio", "", "[sound:old.mp3]") == ""
        assert media_field_text("Word_Audio", "audio", None) == ""
        assert media_field_text("Word_Audio", "audio", "   ") == ""

    def test_current_value_passes_through_unchanged(self):
        """修改時沒動到的欄位會原樣送回：腳本、TG 寫的值不是 web_ 開頭，也不能被擋。"""
        current = "[sound:yuzu_001.ogg]"
        assert media_field_text("Word_Audio", "audio", current, current) == current

    def test_typed_names_are_rejected(self):
        with pytest.raises(MediaFieldInvalidError, match="不能自己輸入"):
            media_field_text("Word_Audio", "audio", "yuzu_001.ogg")
        # 已經包好的寫法也不收：只收純檔名，免得夾帶其他標記
        with pytest.raises(MediaFieldInvalidError):
            media_field_text("Word_Audio", "audio", f"[sound:{self.AUDIO}]")

    def test_wrong_kind_is_rejected(self):
        with pytest.raises(MediaFieldInvalidError, match="要的是音檔"):
            media_field_text("Word_Audio", "audio", self.IMAGE)
        with pytest.raises(MediaFieldInvalidError, match="要的是圖片"):
            media_field_text("Photo", "image", self.AUDIO)


# =============================================================================
# 格式判斷
# =============================================================================


class TestSniff:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (PNG, "png"), (JPG, "jpg"), (WEBP, "webp"), (WAV, "wav"), (OGG, "ogg"), (WEBM, "webm"),
            (M4A, "m4a"), (MP3_ID3, "mp3"), (MP3_FRAME, "mp3"),
        ],
        ids=["png", "jpg", "webp", "wav", "ogg", "webm", "m4a", "mp3-id3", "mp3-frame"],
    )
    def test_known_formats(self, data, expected):
        assert sniff_format(data) == expected

    @pytest.mark.parametrize(
        "data",
        [b"", b"hello, this is a text file renamed to .mp3", b"<html><script>", b"RIFF\x00\x00\x00\x00AVI LIST", HEIC,
         AAC_ADTS, b"\xff\xff\xff\xff", b"GIF89a" + b"\x00" * 16, b"%PDF-1.7"],
        ids=["empty", "text", "html", "avi", "heic", "aac", "ff-noise", "gif", "pdf"],
    )
    def test_everything_else_is_unknown(self, data):
        assert sniff_format(data) is None


# =============================================================================
# 上傳服務：檢查、轉檔、放進暫存區（不碰 Anki）
# =============================================================================


@pytest.fixture
def staging(tmp_path):
    return MediaStaging(tmp_path / "staging")


class TestUpload:
    @pytest.mark.parametrize(
        ("kind", "data", "extension"),
        [("audio", MP3_ID3, "mp3"), ("audio", OGG, "ogg"), ("audio", WAV, "wav"),
         ("image", JPG, "jpg"), ("image", PNG, "png"), ("image", WEBP, "webp")],
    )
    def test_accepted_formats_are_staged_as_they_are(self, staging, kind, data, extension):
        converter = FakeConverter()
        result = _run(_service(staging, converter).upload(kind, data))
        assert NAME_PATTERN.fullmatch(result["filename"]) and result["filename"].endswith(f".{extension}")
        assert result == {"filename": result["filename"], "kind": kind, "size": len(data)}
        assert staging.read(result["filename"]) == data
        assert converter.sources == []  # 不必轉檔

    @pytest.mark.parametrize(("data", "source_name"), [(WEBM, "upload.webm"), (M4A, "upload.m4a")])
    def test_webm_and_m4a_are_converted_to_mp3(self, staging, data, source_name):
        converter = FakeConverter(result=MP3_ID3 + b"converted")
        result = _run(_service(staging, converter).upload("audio", data))
        assert converter.sources == [(source_name, data)]
        assert converter.timeouts == [CONVERT_TIMEOUT_SECONDS]
        assert result["filename"].endswith(".mp3")
        assert result["size"] == len(MP3_ID3 + b"converted")  # 轉檔後的大小
        assert staging.read(result["filename"]) == MP3_ID3 + b"converted"
        assert not converter.paths[0].exists()  # 轉檔用的暫存檔已刪

    def test_size_limit_applies_after_conversion(self, staging):
        """低位元率的錄音轉成 mp3 會變大好幾倍：上限套在實際存下來的大小。"""
        exact = MP3_ID3 + b"\x00" * (MAX_MEDIA_BYTES["audio"] - len(MP3_ID3))
        _run(_service(staging, FakeConverter(result=exact)).upload("audio", WEBM))  # 剛好等於上限可以
        with pytest.raises(MediaTooLargeError, match="轉成 mp3 後"):
            _run(_service(staging, FakeConverter(result=exact + b"\x00")).upload("audio", WEBM))
        assert len(list(staging.root.iterdir())) == 1

    def test_two_uploads_get_different_names(self, staging):
        service = _service(staging)
        first = _run(service.upload("audio", MP3_ID3))
        second = _run(service.upload("audio", MP3_ID3))
        assert first["filename"] != second["filename"]

    @pytest.mark.parametrize(
        ("kind", "data"),
        [("audio", b"just text renamed to .mp3"), ("audio", PNG), ("image", MP3_ID3), ("image", HEIC),
         ("audio", HEIC), ("image", b"GIF89a" + b"\x00" * 16)],
        ids=["disguised-text", "image-as-audio", "audio-as-image", "heic-image", "heic-audio", "gif"],
    )
    def test_wrong_or_unknown_type_is_rejected(self, staging, kind, data):
        with pytest.raises(MediaTypeUnsupportedError):
            _run(_service(staging).upload(kind, data))
        assert not staging.root.exists() or list(staging.root.iterdir()) == []

    def test_empty_file_is_rejected(self, staging):
        with pytest.raises(MediaTypeUnsupportedError, match="空的"):
            _run(_service(staging).upload("audio", b""))

    @pytest.mark.parametrize("kind", ["audio", "image"])
    def test_size_limit(self, staging, kind):
        head = MP3_ID3 if kind == "audio" else PNG
        exact = head + b"\x00" * (MAX_MEDIA_BYTES[kind] - len(head))
        _run(_service(staging).upload(kind, exact))  # 剛好等於上限可以
        with pytest.raises(MediaTooLargeError, match="MB"):
            _run(_service(staging).upload(kind, exact + b"\x00"))
        assert len(list(staging.root.iterdir())) == 1

    def test_broken_file_is_a_conversion_failure(self, staging):
        converter = FakeConverter(error=_broken())
        with pytest.raises(MediaConvertFailedError, match="檔案可能損壞"):
            _run(_service(staging, converter).upload("audio", M4A))
        assert not staging.root.exists() or list(staging.root.iterdir()) == []
        assert not converter.paths[0].exists()  # 失敗時轉檔用的暫存檔也刪了

    def test_empty_conversion_is_a_failure(self, staging):
        with pytest.raises(MediaConvertFailedError, match="空的"):
            _run(_service(staging, FakeConverter(result=b"")).upload("audio", WEBM))

    def test_conversion_timeout_is_a_failure(self, staging):
        failing = FakeConverter(error=FfmpegTimeoutError("FFmpeg 執行超過 90 秒"))
        with pytest.raises(MediaConvertFailedError, match="秒"):
            _run(_service(staging, failing).upload("audio", WEBM))

    def test_missing_ffmpeg_is_a_server_problem(self, staging):
        """找不到 ffmpeg 是伺服器的問題（503），不是叫使用者換檔案的 422。"""

        def no_ffmpeg():
            raise FileNotFoundError("找不到 FFmpeg 執行檔: ffmpeg")

        with pytest.raises(MediaConverterUnavailableError, match="沒有 ffmpeg"):
            _run(MediaService(staging, converter_factory=no_ffmpeg).upload("audio", WEBM))
        # 不必轉檔的格式不需要 ffmpeg
        _run(MediaService(staging, converter_factory=no_ffmpeg).upload("audio", MP3_ID3))
        assert len(list(staging.root.iterdir())) == 1

    def test_ffmpeg_that_cannot_start_is_a_server_problem(self, staging):
        """子程序沒啟動（``return_code`` 為 None，例如權限不足）也不是檔案的問題。"""
        failing = FakeConverter(error=FfmpegError("無法啟動 FFmpeg 子程序: [Errno 13] Permission denied"))
        with pytest.raises(MediaConverterUnavailableError, match="無法執行 ffmpeg"):
            _run(_service(staging, failing).upload("audio", WEBM))

    def test_read_staged(self, staging):
        name = _run(_service(staging).upload("audio", MP3_ID3))["filename"]
        assert _service(staging).read_staged(name) == MP3_ID3
        with pytest.raises(MediaFileNotFoundError):
            _service(staging).read_staged("web_audio_20261020_153012_3f9a1c.mp3")


# =============================================================================
# 暫存區
# =============================================================================


class TestStaging:
    NAME = "web_audio_20261020_153012_3f9a1c.mp3"

    def test_put_read_discard(self, staging):
        staging.put(self.NAME, MP3_ID3)
        assert staging.read(self.NAME) == MP3_ID3
        staging.discard(self.NAME)
        assert staging.read(self.NAME) is None
        staging.discard(self.NAME)  # 不存在時不做事

    @pytest.mark.parametrize("name", ["../x.mp3", "a.mp3", "web_audio_20261020_153012_3f9a1c.exe", ""])
    def test_only_generated_names(self, staging, name):
        """只認後端產生的檔名：不會讀寫暫存目錄以外的東西。"""
        with pytest.raises(ValueError):
            staging.put(name, MP3_ID3)
        assert staging.read(name) is None

    def test_expired_files_are_cleaned_up(self, staging):
        staging.put(self.NAME, MP3_ID3)
        staging.cleanup_expired(now=time.time() + STAGING_TTL_SECONDS - 60)
        assert staging.read(self.NAME) == MP3_ID3  # 還沒過期
        staging.cleanup_expired(now=time.time() + STAGING_TTL_SECONDS + 60)
        assert staging.read(self.NAME) is None


# =============================================================================
# 建卡、修改時存進 Anki，失敗時撤回
# =============================================================================


class CommitAnki(MediaAnki):
    """會記住媒體資料夾、能查「哪張筆記用到這個檔名」的假 Anki。"""

    def __init__(self, used: dict[str, list[int]] | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.media: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.used = used or {}
        self.find_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.fail_on: str | None = None

    async def store_media_file(self, params: AnkiStoreMediaParams) -> str:
        if self.fail_on and params.filename == self.fail_on:
            raise AnkiConnectError("Anki 沒有回應")
        name = await super().store_media_file(params)
        self.media[name] = base64.b64decode(params.data or "")
        return name

    async def find_notes(self, query: str) -> list[int]:
        if self.find_error:
            raise self.find_error
        term = query.strip('"').replace("\\", "")
        return [nid for name, ids in self.used.items() if term == name for nid in ids]

    async def delete_media_file(self, filename: str) -> None:
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(filename)
        self.media.pop(filename, None)


A1 = "web_audio_20261020_153012_aaaaaa.mp3"
A2 = "web_audio_20261020_153013_bbbbbb.mp3"


async def _commit(anki, staging, names, body=None):
    async with committed_media(anki, staging, names):
        if body:
            await body()


class TestCommit:
    def test_staged_names_pick_uploaded_names_only(self):
        values = {"A": f" {A1} ", "B": "[sound:old.mp3]", "C": "", "D": "typed.mp3", "E": A1, "F": None}
        assert staged_names(values, ["A", "B", "C", "D", "E", "F", "missing"]) == [A1]

    def test_success_stores_in_anki_and_drops_staged(self, staging):
        staging.put(A1, MP3_ID3)
        anki = CommitAnki()
        _run(_commit(anki, staging, [A1]))
        assert anki.media == {A1: MP3_ID3}
        assert staging.read(A1) is None

    def test_failure_undoes_and_keeps_staged(self, staging):
        """寫筆記失敗：Anki 回到送出前（檔案刪掉），暫存檔留著，修正後可以直接重送。"""
        staging.put(A1, MP3_ID3)
        anki = CommitAnki()

        async def fail():
            raise DuplicateCardError("重複")

        with pytest.raises(DuplicateCardError):
            _run(_commit(anki, staging, [A1], fail))
        assert anki.media == {} and anki.deleted == [A1]
        assert staging.read(A1) == MP3_ID3
        _run(_commit(anki, staging, [A1]))  # 重送
        assert anki.media == {A1: MP3_ID3}

    def test_failure_keeps_a_file_a_note_already_uses(self, staging):
        """寫入其實成功了（例如回應逾時）：有筆記用到就不刪，不讓卡片指向不存在的檔案。"""
        staging.put(A1, MP3_ID3)
        anki = CommitAnki(used={A1: [42]})

        async def timeout():
            raise AnkiConnectError("逾時")

        with pytest.raises(AnkiConnectError):
            _run(_commit(anki, staging, [A1], timeout))
        assert anki.deleted == [] and A1 in anki.media

    def test_undo_failure_does_not_hide_the_original_error(self, staging):
        staging.put(A1, MP3_ID3)
        anki = CommitAnki()
        anki.find_error = AnkiConnectError("Anki 沒有回應")

        async def fail():
            raise DuplicateCardError("重複")

        with pytest.raises(DuplicateCardError):
            _run(_commit(anki, staging, [A1], fail))
        assert anki.deleted == []  # 確認不了有沒有筆記在用：不刪

    def test_expired_upload_writes_nothing(self, staging):
        staging.put(A1, MP3_ID3)
        anki = CommitAnki()
        with pytest.raises(MediaUploadExpiredError, match=A2):
            _run(_commit(anki, staging, [A1, A2]))
        assert anki.stored == []
        assert staging.read(A1) == MP3_ID3

    def test_second_store_failing_undoes_the_first(self, staging):
        staging.put(A1, MP3_ID3)
        staging.put(A2, MP3_ID3)
        anki = CommitAnki()
        anki.fail_on = A2
        with pytest.raises(AnkiConnectError):
            _run(_commit(anki, staging, [A1, A2]))
        assert anki.media == {} and anki.deleted == [A1]
        assert staging.read(A1) == MP3_ID3 and staging.read(A2) == MP3_ID3

    @pytest.mark.parametrize("renamed", ["renamed_by_anki.mp3", "None"])
    def test_a_renamed_file_is_undone(self, staging, renamed):
        """Anki 改了檔名：欄位裡填的名字對不上，不寫筆記，並刪掉改名存進去的檔案。"""
        staging.put(A1, MP3_ID3)
        anki = CommitAnki(rename=renamed)
        with pytest.raises(AnkiConnectError, match="改了檔名"):
            _run(_commit(anki, staging, [A1]))
        assert anki.deleted == [renamed]


# =============================================================================
# POST /api/v1/media、GET /api/v1/media/{filename}
# =============================================================================


@pytest.fixture
def client(staging):
    from app.main import app

    with (
        mock.patch.object(settings, "API_SECRET_KEY", ""),
        mock.patch.object(media_service, "FfmpegClient", FakeConverter),
        mock.patch.object(media_staging, "root", staging.root),
    ):
        yield TestClient(app)


def _post(client, data: bytes, kind: str | None = "audio", filename: str = "evil name.exe", **kwargs):
    form = {"kind": kind} if kind is not None else {}
    return client.post("/api/v1/media", files={"file": (filename, io.BytesIO(data))}, data=form, **kwargs)


def _error(response) -> tuple[int, str]:
    return response.status_code, response.json().get("error_code")


def _staged(staging) -> list[str]:
    return sorted(p.name for p in staging.root.iterdir()) if staging.root.exists() else []


class TestApi:
    def test_upload_is_staged_not_written_to_anki(self, client, staging):
        response = _post(client, OGG)
        assert response.status_code == 201
        body = response.json()
        assert body == {"filename": body["filename"], "kind": "audio", "size": len(OGG)}
        # 上傳的檔名被忽略，由後端產生
        assert re.fullmatch(r"web_audio_\d{8}_\d{6}_[0-9a-f]{6}\.ogg", body["filename"])
        assert staging.read(body["filename"]) == OGG

    def test_read_back_a_staged_upload(self, client, staging):
        name = _post(client, PNG, kind="image", filename="photo.png").json()["filename"]
        response = client.get(f"/api/v1/media/{name}")
        assert response.status_code == 200 and response.content == PNG
        assert response.headers["content-type"] == "image/png"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"].startswith("sandbox")
        assert response.headers["cache-control"] == "no-store"  # 隨時會被用掉或過期
        assert _error(client.get("/api/v1/media/web_image_20261020_153012_3f9a1c.png")) == (404, "MEDIA_FILE_NOT_FOUND")
        assert client.get("/api/v1/media/..%2Fsecret").status_code == 404

    def test_webm_recording_becomes_mp3(self, client, staging):
        response = _post(client, WEBM, filename="recording.webm")
        assert response.status_code == 201
        assert response.json()["filename"].endswith(".mp3")
        assert staging.read(response.json()["filename"]) == MP3_ID3

    def test_disguised_file_is_415(self, client, staging):
        response = _post(client, b"plain text", filename="song.mp3")
        assert _error(response) == (415, "MEDIA_TYPE_UNSUPPORTED")
        assert "認不出" in response.json()["message"]
        assert _staged(staging) == []

    def test_wrong_kind_is_415(self, client):
        assert _error(_post(client, PNG, kind="audio")) == (415, "MEDIA_TYPE_UNSUPPORTED")

    def test_too_large_is_413(self, client, staging):
        response = _post(client, PNG + b"\x00" * MAX_MEDIA_BYTES["image"], kind="image")
        assert _error(response) == (413, "MEDIA_TOO_LARGE")
        assert _staged(staging) == []

    def test_conversion_failure_is_422(self, client, staging):
        with mock.patch.object(media_service, "FfmpegClient", lambda: FakeConverter(error=_broken("壞掉的檔案"))):
            response = _post(client, WEBM)
        assert _error(response) == (422, "MEDIA_CONVERT_FAILED")
        assert _staged(staging) == []

    def test_missing_ffmpeg_is_503(self, client, staging):
        def no_ffmpeg():
            raise FileNotFoundError("找不到 FFmpeg 執行檔: ffmpeg")

        with mock.patch.object(media_service, "FfmpegClient", no_ffmpeg):
            response = _post(client, WEBM)
        assert _error(response) == (503, "MEDIA_CONVERTER_UNAVAILABLE")

    def test_missing_or_bad_kind_is_422(self, client, staging):
        assert _post(client, MP3_ID3, kind=None).status_code == 422
        assert _post(client, MP3_ID3, kind="video").status_code == 422
        assert client.post("/api/v1/media", data={"kind": "audio"}).status_code == 422
        assert _staged(staging) == []

    def test_requires_api_key(self, client, staging):
        with mock.patch.object(settings, "API_SECRET_KEY", "s3cret"):
            assert _post(client, MP3_ID3).status_code == 401
            assert client.get(f"/api/v1/media/{A1}").status_code == 401
            assert _staged(staging) == []
            assert _post(client, MP3_ID3, headers={"X-API-Key": "s3cret"}).status_code == 201


# =============================================================================
# 真的 ffmpeg（有才跑）
# =============================================================================


def _wav_bytes(seconds: float = 0.3) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(b"\x00\x10" * int(8000 * seconds))
    return buffer.getvalue()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="這台機器沒有 ffmpeg")
class TestRealFfmpeg:
    @pytest.mark.parametrize(("container", "codec"), [("webm", "libopus"), ("m4a", "aac")])
    def test_browser_recordings_convert_to_playable_mp3(self, tmp_path, staging, container, codec):
        """瀏覽器錄音的兩種格式（Chrome／Firefox 的 webm、Safari 的 m4a）實際轉一次。"""
        source = tmp_path / "in.wav"
        source.write_bytes(_wav_bytes())
        recorded = tmp_path / f"recorded.{container}"
        client = FfmpegClient()
        _run(client._run_command(["-y", "-i", str(source), "-c:a", codec, str(recorded)]))
        data = recorded.read_bytes()
        assert sniff_format(data) == container

        result = _run(MediaService(staging).upload("audio", data))
        assert str(result["filename"]).endswith(".mp3")
        assert sniff_format(staging.read(str(result["filename"])) or b"") == "mp3"

    def test_broken_file_is_a_conversion_error(self, staging):
        with pytest.raises(MediaConvertFailedError):
            _run(MediaService(staging).upload("audio", WEBM))

    def test_timeout_kills_ffmpeg(self):
        """逾時要中止 ffmpeg：讀一個永遠不結束的輸入（anullsrc），0.5 秒後應該拋逾時而不是卡住。"""
        client = FfmpegClient()
        args = ["-f", "lavfi", "-i", "anullsrc", "-f", "null", "-"]
        with pytest.raises(FfmpegTimeoutError):
            _run(client._run_command(args, timeout=0.5))
