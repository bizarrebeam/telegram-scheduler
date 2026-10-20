import logging

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import HTMLResponse

from app.google.oauth import exchange_code_for_user, pop_valid_state

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/oauth/google/callback", response_class=HTMLResponse)
async def google_callback(request: Request) -> str:
    params = request.query_params
    code = params.get("code")
    state = params.get("state")
    error = params.get("error")
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code/state")
    parsed = pop_valid_state(state)
    if parsed is None:
        raise HTTPException(status_code=400, detail="Invalid or expired state")
    user_id, code_verifier = parsed
    try:
        exchange_code_for_user(code, user_id, code_verifier)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Failed to exchange token: {exc}")
    tg_app = getattr(request.app.state, "tg_app", None)
    if tg_app is not None:
        try:
            await tg_app.bot.send_message(
                chat_id=user_id,
                text="Google Calendar connected successfully.\n\nYou can return to Telegram.",
            )
        except Exception:  # noqa: BLE001
            logger.warning("failed to notify telegram user after oauth connect")
    return (
        "<html><head><meta charset=\"utf-8\"></head><body>"
        "<h1>Google Calendar connected successfully.</h1>"
        "<p>You can close this page and return to Telegram.</p>"
        "</body></html>"
    )
