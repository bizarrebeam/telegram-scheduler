import re
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

_PATTERNS = (
    (re.compile(r"\b(\d+)\s*(?:jam|hours?|hrs?)\b", re.I), 60),
    (re.compile(r"\b(\d+)\s*(?:menit|minutes?|mins?)\b", re.I), 1),
)

DEFAULT_ESTIMATE_MINUTES = 60
MAX_TITLE_LENGTH = 100
JAKARTA = ZoneInfo("Asia/Jakarta")

_EXTENSION_PATTERNS = (
    re.compile(
        r"^\s*\+\s*(\d{1,3})\s*(?:menit|m|min|mins|minutes?)?\s*$", re.I
    ),
    re.compile(
        r"\b(?:tambah|butuh|perlu|nambah|add|need|extend)\s+(\d{1,3})\s*"
        r"(?:menit|m|min|mins|minutes?)\b",
        re.I,
    ),
    re.compile(r"\b(\d{1,3})\s+more\s+(?:menit|m|min|mins|minutes?)\b", re.I),
    re.compile(r"\b(?:extend|perpanjang)\s+(\d{1,3})\b", re.I),
)

_OVERWHELM_PATTERNS = (
    re.compile(r"\b(bingung|kewalahan|overwhelm\w*|bantu pilih|bantu tentukan)\b", re.I),
    re.compile(r"\bmulai dari mana\b", re.I),
    re.compile(r"\btidak tahu harus (?:ngapain|apa)\b", re.I),
    re.compile(r"\b(?:i'?m |i am |feeling )?stuck\b", re.I),
    re.compile(r"\blocked\b", re.I),
    re.compile(r"\bdon'?t know (?:where|what) to (?:start|do)\b", re.I),
    re.compile(r"\bwhat should i do\b", re.I),
)

_REMINDER_ONLY_PATTERNS = (
    re.compile(
        r"\b(?:reminder only|just remind(?: me)?|only remind(?: me)?|"
        r"hanya pengingat|pengingat saja|cuma pengingat|tanpa jadwal|"
        r"jangan jadwalkan|no work session|don'?t schedule)\b",
        re.I,
    ),
)

_DEADLINE_KEYWORD = re.compile(
    r"\b(?:sampai|hingga|deadline|due|paling lambat|sebelum|by|before|tutup|"
    r"batas akhir|last date)\b",
    re.I,
)

_MONTHS = {
    "januari": 1,
    "january": 1,
    "jan": 1,
    "februari": 2,
    "february": 2,
    "feb": 2,
    "maret": 3,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "mei": 5,
    "may": 5,
    "juni": 6,
    "june": 6,
    "jun": 6,
    "juli": 7,
    "july": 7,
    "jul": 7,
    "agustus": 8,
    "august": 8,
    "agu": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "oktober": 10,
    "october": 10,
    "okt": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "desember": 12,
    "december": 12,
    "des": 12,
    "dec": 12,
}

_DEADLINE_RE = re.compile(
    r"\b(?:sampai|hingga|deadline|due|paling lambat|sebelum|by|before|tutup|"
    r"batas akhir|last date)\s+(?:tanggal\s+)?"
    r"(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{4}))?",
    re.I,
)

MAX_EXTENSION_MINUTES = 240
_URL_RE = re.compile(r"https?://\S+", re.I)


@dataclass(frozen=True)
class ParsedMessage:
    title: str
    description: str
    estimated_duration_minutes: int
    duration_source: str
    category: str
    first_step: str | None = None
    deadline_at: datetime | None = None
    deadline_date: date | None = None
    deadline_unclear: bool = False
    reminder_only: bool = False


def _year_for(month: int, day: int, today: date, year: int | None) -> int | None:
    if year is not None:
        return year
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return None
    if candidate < today:
        return today.year + 1
    return today.year


def _extract_deadline(
    text: str, today: date
) -> tuple[date | None, str, bool]:
    match = _DEADLINE_RE.search(text)
    if match is None:
        unclear = bool(_DEADLINE_KEYWORD.search(text))
        return None, text, unclear
    month = _MONTHS.get(match.group(2).lower())
    if month is None:
        return None, text, True
    day = int(match.group(1))
    year = int(match.group(3)) if match.group(3) else None
    try:
        resolved = date(
            _year_for(month, day, today, year) or today.year, month, day
        )
    except ValueError:
        return None, text, True
    cleaned = (text[: match.start()] + " " + text[match.end() :]).strip()
    cleaned = " ".join(cleaned.split()).strip(" ,.;-")
    return resolved, cleaned, False


def _deadline_end(day: date) -> datetime:
    return datetime.combine(day, time(23, 59), tzinfo=JAKARTA)


def parse_captured_message(
    text: str, today: date | None = None
) -> ParsedMessage:
    cleaned = " ".join(text.split())
    today = today or datetime.now(JAKARTA).date()
    deadline_date, title_text, unclear = _extract_deadline(cleaned, today)
    minutes = 0
    for pattern, unit in _PATTERNS:
        match = pattern.search(cleaned)
        if match:
            minutes += int(match.group(1)) * unit
    if minutes > 0:
        duration_source = "user"
    else:
        minutes = DEFAULT_ESTIMATE_MINUTES
        duration_source = "estimate"
    title_source = " ".join(_URL_RE.sub(" ", title_text).split()).strip()
    title = (
        title_source
        or ("Deadline reminder" if deadline_date is not None else "Saved link")
    )[:MAX_TITLE_LENGTH]
    return ParsedMessage(
        title=title,
        description=cleaned,
        estimated_duration_minutes=minutes,
        duration_source=duration_source,
        category="other",
        deadline_at=_deadline_end(deadline_date) if deadline_date else None,
        deadline_date=deadline_date,
        deadline_unclear=unclear,
        reminder_only=is_reminder_only(cleaned),
    )


def parse_extension(text: str) -> int | None:
    cleaned = " ".join(text.split())
    for pattern in _EXTENSION_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            minutes = int(match.group(1))
            if 0 < minutes <= MAX_EXTENSION_MINUTES:
                return minutes
    return None


def is_overwhelm(text: str) -> bool:
    cleaned = " ".join(text.split())
    return any(pattern.search(cleaned) for pattern in _OVERWHELM_PATTERNS)


def is_reminder_only(text: str) -> bool:
    cleaned = " ".join(text.split())
    return any(pattern.search(cleaned) for pattern in _REMINDER_ONLY_PATTERNS)


def deadline_end(day: date) -> datetime:
    return _deadline_end(day)
_DEADLINE_DATE_RE = re.compile(
    r"^\s*(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{4}))?\s*$"
)


def parse_deadline_date(text: str, today: date | None = None) -> date | None:
    cleaned = " ".join(text.split())
    today = today or datetime.now(JAKARTA).date()
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        pass
    match = _DEADLINE_DATE_RE.match(cleaned)
    if match is None:
        return None
    month = _MONTHS.get(match.group(2).lower())
    if month is None:
        return None
    day = int(match.group(1))
    year = int(match.group(3)) if match.group(3) else None
    try:
        return date(_year_for(month, day, today, year) or today.year, month, day)
    except ValueError:
        return None
