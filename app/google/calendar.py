from datetime import date, datetime, timedelta

import httpx
from google.oauth2.credentials import Credentials

CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleAuthRequired(Exception):
    pass


class EventConflict(Exception):
    pass


def _headers(creds: Credentials) -> dict:
    return {"Authorization": f"Bearer {creds.token}"}


def _check(resp: httpx.Response, what: str) -> None:
    if resp.status_code in (401, 403):
        raise GoogleAuthRequired(
            f"{what}: HTTP {resp.status_code} \u2014 Google login needs to be repeated (/auth)"
        )
    resp.raise_for_status()


def get_busy_intervals(
    creds: Credentials,
    time_min: datetime,
    time_max: datetime,
    calendar_id: str = "primary",
) -> list[tuple[datetime, datetime]]:
    payload = {
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "items": [{"id": calendar_id}],
    }
    resp = httpx.post(
        f"{CALENDAR_BASE}/freeBusy",
        json=payload,
        headers=_headers(creds),
        timeout=30,
    )
    _check(resp, "freeBusy")
    data = resp.json()
    entry = data.get("calendars", {}).get(calendar_id, {})
    errors = entry.get("errors") or []
    if errors:
        raise RuntimeError(f"freeBusy error: {errors[0].get('message', 'unknown')}")
    intervals = []
    for busy in entry.get("busy", []):
        start = datetime.fromisoformat(busy["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(busy["end"].replace("Z", "+00:00"))
        intervals.append((start, end))
    return intervals


def create_event(
    creds: Credentials,
    calendar_id: str,
    *,
    summary: str,
    description: str,
    start: datetime,
    end: datetime,
    event_id: str | None = None,
    timezone: str = "Asia/Jakarta",
) -> str:
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": timezone},
        "end": {"dateTime": end.isoformat(), "timeZone": timezone},
    }
    if event_id:
        body["id"] = event_id
    resp = httpx.post(
        f"{CALENDAR_BASE}/calendars/{calendar_id}/events",
        json=body,
        headers=_headers(creds),
        timeout=30,
    )
    if resp.status_code == 409:
        raise EventConflict(f"event {event_id} already exists")
    _check(resp, "createEvent")
    return resp.json()["id"]


def get_event(
    creds: Credentials, calendar_id: str, event_id: str
) -> dict | None:
    resp = httpx.get(
        f"{CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}",
        headers=_headers(creds),
        timeout=30,
    )
    if resp.status_code == 404:
        return None
    _check(resp, "getEvent")
    return resp.json()


def update_event_time(
    creds: Credentials,
    calendar_id: str,
    event_id: str,
    *,
    start: datetime,
    end: datetime,
    timezone: str = "Asia/Jakarta",
) -> bool:
    resp = httpx.patch(
        f"{CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}",
        json={
            "start": {"dateTime": start.isoformat(), "timeZone": timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": timezone},
        },
        headers=_headers(creds),
        timeout=30,
    )
    if resp.status_code in (404, 410):
        return False
    _check(resp, "updateEventTime")
    return True


def delete_event(creds: Credentials, calendar_id: str, event_id: str) -> bool:
    resp = httpx.delete(
        f"{CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}",
        headers=_headers(creds),
        timeout=30,
    )
    if resp.status_code in (404, 410):
        return False
    _check(resp, "deleteEvent")
    return True


def create_all_day_event(
    creds: Credentials,
    calendar_id: str,
    *,
    summary: str,
    description: str,
    event_date: date,
    event_id: str | None = None,
) -> str:
    body = {
        "summary": summary,
        "description": description,
        "start": {"date": event_date.isoformat()},
        "end": {"date": (event_date + timedelta(days=1)).isoformat()},
        "transparency": "transparent",
    }
    if event_id:
        body["id"] = event_id
    resp = httpx.post(
        f"{CALENDAR_BASE}/calendars/{calendar_id}/events",
        json=body,
        headers=_headers(creds),
        timeout=30,
    )
    if resp.status_code == 409:
        raise EventConflict(f"event {event_id} already exists")
    _check(resp, "createAllDayEvent")
    return resp.json()["id"]


def update_event_date(
    creds: Credentials,
    calendar_id: str,
    event_id: str,
    *,
    event_date: date,
) -> bool:
    resp = httpx.patch(
        f"{CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}",
        json={
            "start": {"date": event_date.isoformat()},
            "end": {"date": (event_date + timedelta(days=1)).isoformat()},
        },
        headers=_headers(creds),
        timeout=30,
    )
    if resp.status_code in (404, 410):
        return False
    _check(resp, "updateEventDate")
    return True

