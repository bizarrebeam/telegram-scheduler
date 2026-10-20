import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from telegram import Update

from app.config import get_settings
from app.telegram_bot.bot import build_application
from app.web.oauth_routes import router
from app.workers.runner import run_worker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    tg_app = None
    worker_task = None
    if settings.telegram_bot_token and settings.allowed_telegram_user_ids:
        tg_app = build_application(settings.telegram_bot_token)
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        worker_task = asyncio.create_task(run_worker(tg_app))
        logger.info("telegram bot polling started")
    else:
        logger.warning("telegram bot not started: token or allowlist missing")
    app.state.tg_app = tg_app
    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
        if tg_app is not None:
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()


app = FastAPI(title="telegram-scheduler", version="0.1.0", lifespan=lifespan)

app.include_router(router)


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    return {"status": "ok", "env": settings.app_env, "timezone": settings.timezone}
