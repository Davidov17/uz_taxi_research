"""Persists uploaded screenshots from Telegram's servers to local disk
(a Docker volume in production) so they survive independently of
Telegram's own file cache and can be served by the admin dashboard.

Kept out of application/survey_session.py deliberately: that layer has no
Bot/network dependency today (it's exercised directly in tests with a
plain DB session), and downloading a file is an I/O side effect that
belongs in the presentation layer, which already has the Bot instance.
"""

import logging
import mimetypes
from pathlib import Path

from aiogram import Bot

logger = logging.getLogger(__name__)

_EXT_BY_FILE_TYPE_FALLBACK = ".jpg"


def attachment_path(storage_dir: str, survey_id, attachment_id: int, mime_type: str | None) -> Path:
    ext = (mimetypes.guess_extension(mime_type) if mime_type else None) or _EXT_BY_FILE_TYPE_FALLBACK
    return Path(storage_dir) / str(survey_id) / f"{attachment_id}{ext}"


async def download_attachment(
    bot: Bot,
    *,
    storage_dir: str,
    survey_id,
    attachment_id: int,
    telegram_file_id: str,
    mime_type: str | None,
) -> str | None:
    """Best-effort download. Returns the local path on success, or None on
    any failure (network hiccup, Telegram API error, disk full) — a driver
    mid-survey must never see an error because caching a copy failed; the
    Telegram file_id/file_unique_id already saved to Attachment is the
    durable record regardless.
    """
    dest = attachment_path(storage_dir, survey_id, attachment_id, mime_type)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        await bot.download(telegram_file_id, destination=dest)
    except Exception:
        logger.warning("Failed to cache attachment %s locally", attachment_id, exc_info=True)
        return None
    return str(dest)
