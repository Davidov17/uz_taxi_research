"""Unit tests for infrastructure/storage.py — the screenshot-to-disk
caching helper added for production deployment (persistent volume storage,
see docs/DEPLOYMENT.md). Exercised directly against a fake bot rather than
through the full Telegram dispatcher: the handler-level tests
(test_screenshot_workflow.py, test_bot_edge_cases.py) already cover that
a failed download never breaks the survey flow; these tests cover the
helper's own success/failure/path-naming behavior in isolation.
"""

import uuid
from pathlib import Path

import pytest

from telegram_bot.infrastructure.storage import attachment_path, download_attachment


class _FakeBotDownloads:
    def __init__(self, *, content: bytes = b"fake-image-bytes", raises: bool = False):
        self.content = content
        self.raises = raises
        self.calls: list[tuple[str, Path]] = []

    async def download(self, file_id: str, destination: Path) -> None:
        self.calls.append((file_id, destination))
        if self.raises:
            raise RuntimeError("simulated Telegram API failure")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.content)


def test_attachment_path_uses_mime_extension(tmp_path):
    survey_id = uuid.uuid4()
    path = attachment_path(str(tmp_path), survey_id, 42, "image/png")
    assert path == tmp_path / str(survey_id) / "42.png"


def test_attachment_path_falls_back_to_jpg_without_mime(tmp_path):
    survey_id = uuid.uuid4()
    path = attachment_path(str(tmp_path), survey_id, 7, None)
    assert path.suffix == ".jpg"


@pytest.mark.asyncio
async def test_download_attachment_success_writes_file_and_returns_path(tmp_path):
    bot = _FakeBotDownloads()
    survey_id = uuid.uuid4()

    result = await download_attachment(
        bot,
        storage_dir=str(tmp_path),
        survey_id=survey_id,
        attachment_id=1,
        telegram_file_id="tg-file-1",
        mime_type="image/jpeg",
    )

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()
    assert result_path.read_bytes() == b"fake-image-bytes"
    assert bot.calls == [("tg-file-1", result_path)]


@pytest.mark.asyncio
async def test_download_attachment_failure_returns_none_and_does_not_raise(tmp_path):
    bot = _FakeBotDownloads(raises=True)
    survey_id = uuid.uuid4()

    result = await download_attachment(
        bot,
        storage_dir=str(tmp_path),
        survey_id=survey_id,
        attachment_id=2,
        telegram_file_id="tg-file-2",
        mime_type="image/png",
    )

    assert result is None
