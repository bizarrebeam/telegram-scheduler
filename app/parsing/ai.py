import json
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings
from app.parsing.basic import (
    DEFAULT_ESTIMATE_MINUTES,
    MAX_TITLE_LENGTH,
    ParsedMessage,
    parse_captured_message,
)

logger = logging.getLogger(__name__)

ALLOWED_CATEGORIES = {
    "work",
    "learning",
    "personal_admin",
    "routine",
    "leisure",
    "other",
}
MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 600

SYSTEM_PROMPT = (
    "You extract tasks from Telegram messages written in English or Indonesian. "
    "Reply with ONLY a JSON object with these keys: title (short string, in the "
    "language the user wrote, never containing a URL), description (string, keep "
    "the original message and any links here), category (one of: work, "
    "learning, personal_admin, routine, leisure, other), "
    "estimated_duration_minutes (integer 5-600), first_step (small concrete "
    "string, may be empty), deadline_at (ISO-8601 datetime at the end of the "
    "deadline day, or null)."
)


def _coerce_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _coerce_deadline(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _validate(payload: dict, fallback: ParsedMessage) -> ParsedMessage:
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        return fallback
    category = payload.get("category")
    if category not in ALLOWED_CATEGORIES:
        category = "other"
    duration = _coerce_int(payload.get("estimated_duration_minutes"))
    duration_source = "user"
    if duration is None or not MIN_DURATION_MINUTES <= duration <= MAX_DURATION_MINUTES:
        duration = fallback.estimated_duration_minutes or DEFAULT_ESTIMATE_MINUTES
        duration_source = fallback.duration_source
    first_step = payload.get("first_step")
    if not isinstance(first_step, str) or not first_step.strip():
        first_step = None
    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        description = fallback.description
    deadline = _coerce_deadline(payload.get("deadline_at"))
    if deadline is not None:
        deadline_date = deadline.astimezone(ZoneInfo("Asia/Jakarta")).date()
    else:
        deadline_date = fallback.deadline_date
        deadline = fallback.deadline_at
    return ParsedMessage(
        title=title.strip()[:MAX_TITLE_LENGTH],
        description=description.strip(),
        estimated_duration_minutes=duration,
        duration_source=duration_source,
        category=category,
        first_step=first_step.strip() if first_step else None,
        deadline_at=deadline,
        deadline_date=deadline_date,
        deadline_unclear=False if deadline_date else fallback.deadline_unclear,
        reminder_only=fallback.reminder_only,
    )


def parse_with_ai(text: str) -> ParsedMessage | None:
    settings = get_settings()
    if not settings.openai_api_key:
        return None
    body = {
        "model": settings.openai_model,
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }
    try:
        resp = httpx.post(
            f"{settings.openai_base_url.rstrip('/')}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            timeout=20,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError("AI payload is not an object")
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai parse failed, using fallback parser: %s", exc)
        return None
    return _validate(payload, parse_captured_message(text))


def parse_message(text: str) -> ParsedMessage:
    return parse_with_ai(text) or parse_captured_message(text)


