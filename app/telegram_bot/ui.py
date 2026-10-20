from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

DAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
MONTH_NAMES = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def to_local(moment: datetime, timezone: str) -> datetime:
    return moment.astimezone(ZoneInfo(timezone))


def fmt_time(moment: datetime, timezone: str) -> str:
    return to_local(moment, timezone).strftime("%H:%M")


def fmt_datetime(moment: datetime, timezone: str) -> str:
    local = to_local(moment, timezone)
    return (
        f"{DAY_NAMES[local.weekday()]}, {local.day:02d} "
        f"{MONTH_NAMES[local.month - 1]}, {local:%H:%M}"
    )


def fmt_short_datetime(moment: datetime, timezone: str) -> str:
    local = to_local(moment, timezone)
    return f"{local.day:02d} {MONTH_NAMES[local.month - 1]}, {local:%H:%M}"


def fmt_range(start: datetime, end: datetime, timezone: str) -> str:
    return f"{fmt_datetime(start, timezone)}\u2013{fmt_time(end, timezone)}"


def proposal_text(
    *,
    task_title: str,
    start: datetime,
    end: datetime,
    duration_minutes: int,
    deadline_at: datetime | None,
    timezone: str,
) -> str:
    lines = [
        f"Proposed session: {task_title}",
        f"{fmt_range(start, end, timezone)} \u00b7 {duration_minutes} minutes",
    ]
    if deadline_at:
        lines.append(f"Deadline: {fmt_datetime(deadline_at, timezone)}")
    lines.append(
        "Does this time work for you? I'll add it to Google Calendar after you approve."
    )
    return "\n".join(lines)


def proposal_keyboard(proposal_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Approve", callback_data=f"approve:{proposal_id}"),
                InlineKeyboardButton("Decline", callback_data=f"reject:{proposal_id}"),
            ],
            [
                InlineKeyboardButton(
                    "Find another time", callback_data=f"another:{proposal_id}"
                )
            ],
            [InlineKeyboardButton("Back to today", callback_data="today")],
        ]
    )


def session_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Done", callback_data=f"done:{session_id}"),
                InlineKeyboardButton("+30 minutes", callback_data=f"addtime:{session_id}"),
            ],
            [
                InlineKeyboardButton(
                    "Reschedule", callback_data=f"reschedule:{session_id}"
                ),
                InlineKeyboardButton("Skip", callback_data=f"skip:{session_id}"),
            ],
        ]
    )


def missed_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Start now", callback_data=f"startnow:{session_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "Reschedule", callback_data=f"reschedule:{session_id}"
                ),
                InlineKeyboardButton("Skip", callback_data=f"skip:{session_id}"),
            ],
        ]
    )


def task_keyboard(task_short_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Find a time", callback_data=f"schedule:{task_short_id}"
                ),
                InlineKeyboardButton("View task", callback_data=f"view:{task_short_id}"),
            ]
        ]
    )


def overwhelm_keyboard(
    task_short_id: str, session_id: str | None = None
) -> InlineKeyboardMarkup:
    rows = []
    if session_id:
        rows.append(
            [
                InlineKeyboardButton(
                    "Start now", callback_data=f"startnow:{session_id}"
                ),
                InlineKeyboardButton(
                    "Schedule", callback_data=f"schedule:{task_short_id}"
                ),
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    "Schedule", callback_data=f"schedule:{task_short_id}"
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton("View task", callback_data=f"view:{task_short_id}"),
            InlineKeyboardButton(
                "Choose another task", callback_data=f"nexttask:{task_short_id}"
            ),
        ]
    )
    return InlineKeyboardMarkup(rows)


def reschedule_offer_keyboard(task_short_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Find a time", callback_data=f"schedule:{task_short_id}"
                )
            ],
            [InlineKeyboardButton("Cancel", callback_data="dismiss")],
        ]
    )


def start_keyboard(task_short_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Schedule", callback_data=f"schedule:{task_short_id}"
                ),
                InlineKeyboardButton("View task", callback_data=f"view:{task_short_id}"),
            ]
        ]
    )


def fmt_deadline_date(day) -> str:
    return (
        f"{DAY_NAMES[day.weekday()]}, {day.day:02d} "
        f"{MONTH_NAMES[day.month - 1]} {day.year}"
    )


def deadline_text(
    task_title: str,
    event_date,
    duration_minutes: int,
    duration_source: str,
    preparation_scheduled: bool = False,
) -> str:
    estimate = "provisional" if duration_source == "estimate" else "your estimate"
    lines = [
        "Deadline reminder",
        "",
        task_title,
        "",
        f"Due: {fmt_deadline_date(event_date)} (all-day)",
        f"Preparation estimate: {duration_minutes} minutes ({estimate})",
    ]
    if preparation_scheduled:
        lines += ["", "A separate preparation proposal is below."]
    else:
        lines += [
            "",
            "Approving the deadline only creates the all-day reminder. "
            "It does not schedule work.",
        ]
    return "\n".join(lines)


def deadline_keyboard(reminder_id: str, task_short_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Approve deadline",
                    callback_data=f"deadline_ok:{reminder_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "Schedule preparation",
                    callback_data=f"prep:{task_short_id}",
                ),
                InlineKeyboardButton(
                    "Reminder only",
                    callback_data=f"deadline_only:{reminder_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "Edit deadline",
                    callback_data=f"deadline_edit:{task_short_id}",
                ),
                InlineKeyboardButton(
                    "Cancel",
                    callback_data=f"deadline_no:{reminder_id}",
                ),
            ],
        ]
    )


def task_details_keyboard(
    task_short_id: str,
    session_id: str | None = None,
    startable: bool = False,
) -> InlineKeyboardMarkup:
    if session_id:
        first_row = [
            InlineKeyboardButton("Done", callback_data=f"taskdone:{task_short_id}"),
            InlineKeyboardButton(
                "Reschedule", callback_data=f"reschedule:{session_id}"
            ),
        ]
        if startable:
            first_row.insert(
                0,
                InlineKeyboardButton("Start", callback_data=f"startnow:{session_id}"),
            )
        second_row = [
            InlineKeyboardButton(
                "Cancel task", callback_data=f"cancel:{task_short_id}"
            ),
            InlineKeyboardButton("Files", callback_data=f"files:{task_short_id}"),
        ]
    else:
        first_row = [
            InlineKeyboardButton("Schedule", callback_data=f"schedule:{task_short_id}"),
            InlineKeyboardButton("Done", callback_data=f"taskdone:{task_short_id}"),
        ]
        second_row = [
            InlineKeyboardButton(
                "Cancel task", callback_data=f"cancel:{task_short_id}"
            ),
            InlineKeyboardButton("Files", callback_data=f"files:{task_short_id}"),
        ]
    return InlineKeyboardMarkup(
        [
            first_row,
            second_row,
            [
                InlineKeyboardButton("Back to today", callback_data="today"),
                InlineKeyboardButton(
                    "Choose another task", callback_data="picktask:0"
                ),
            ],
            [
                InlineKeyboardButton(
                    "Copy task ID",
                    copy_text=CopyTextButton(text=task_short_id),
                )
            ],
        ]
    )


def today_keyboard(
    task_short_id: str,
    session_id: str | None = None,
    startable: bool = False,
    has_agenda: bool = False,
) -> InlineKeyboardMarkup:
    if session_id:
        first_row = [
            InlineKeyboardButton(
                "Reschedule", callback_data=f"reschedule:{session_id}"
            )
        ]
        if startable:
            first_row.insert(
                0,
                InlineKeyboardButton("Start", callback_data=f"startnow:{session_id}"),
            )
    else:
        first_row = [
            InlineKeyboardButton(
                "Schedule", callback_data=f"schedule:{task_short_id}"
            )
        ]
    rows = [
        first_row,
        [
            InlineKeyboardButton(
                "Choose another task", callback_data="picktask:0"
            ),
            InlineKeyboardButton("View task", callback_data=f"view:{task_short_id}"),
        ],
    ]
    if has_agenda:
        rows.append([InlineKeyboardButton("View agenda", callback_data="agenda")])
    return InlineKeyboardMarkup(rows)


def selection_keyboard(
    task_short_id: str,
    session_id: str | None = None,
    startable: bool = False,
) -> InlineKeyboardMarkup:
    if session_id:
        first_row = [
            InlineKeyboardButton(
                "Reschedule", callback_data=f"reschedule:{session_id}"
            )
        ]
        if startable:
            first_row.insert(
                0,
                InlineKeyboardButton("Start", callback_data=f"startnow:{session_id}"),
            )
    else:
        first_row = [
            InlineKeyboardButton(
                "Schedule", callback_data=f"schedule:{task_short_id}"
            )
        ]
    return InlineKeyboardMarkup(
        [
            first_row,
            [
                InlineKeyboardButton("View task", callback_data=f"view:{task_short_id}"),
                InlineKeyboardButton(
                    "Choose another task", callback_data="picktask:0"
                ),
            ],
            [InlineKeyboardButton("Back to today", callback_data="today")],
        ]
    )


def choose_task_keyboard(
    rows: list[tuple],
    page: int,
    page_size: int,
    total: int | None = None,
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                f"Choose this task", callback_data=f"selecttask:{short}"
            )
        ]
        for short, _title, _minutes in rows
    ]
    pages = max(1, -(-(total if total is not None else len(rows)) // page_size))
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton("Previous", callback_data=f"picktask:{page - 1}")
            )
        if page < pages - 1:
            nav.append(
                InlineKeyboardButton("Next", callback_data=f"picktask:{page + 1}")
            )
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("Back to today", callback_data="today")])
    return InlineKeyboardMarkup(buttons)


def agenda_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Back to today", callback_data="today")]]
    )


def after_action_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Back to today", callback_data="today")],
            [
                InlineKeyboardButton(
                    "Choose another task", callback_data="picktask:0"
                )
            ],
        ]
    )


def queue_keyboard(
    rows: list[tuple], page: int, page_size: int, total: int | None = None
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("Schedule", callback_data=f"schedule:{short}"),
            InlineKeyboardButton("View", callback_data=f"view:{short}"),
        ]
        for short, _title, _minutes in rows
    ]
    pages = max(1, -(-(total if total is not None else len(rows)) // page_size))
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton("Previous", callback_data=f"page:{page - 1}")
            )
        if page < pages - 1:
            nav.append(InlineKeyboardButton("Next", callback_data=f"page:{page + 1}"))
        buttons.append(nav)
    return InlineKeyboardMarkup(buttons)


def short_id(value) -> str:
    return str(value)[:8]

