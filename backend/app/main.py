import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import PRODUCT_NAME, get_settings, media_root
from app.routers import (
    activity, assistant, audit, auth, badges, broadcast, cameras, demo, duty, events, guards, health, internal,
    invites, overview, responders, roles, roll_call, share, sites, stats, stream, summary, twilio, users, zones,
)
from app.services.calls import calls
from app.services.escalation import escalation
from app.services.workers import workers
from app.ws import manager

settings = get_settings()
log = logging.getLogger("safetyai.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Start the background parts with the API, stop them with it."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("apscheduler").setLevel(logging.WARNING)  # it logs every finished timer job otherwise
    manager.loop = asyncio.get_running_loop()  # lets timer threads push WebSocket updates
    escalation.start_background()  # call-ladder timers, 18:00 digest, camera heartbeat watchdog
    calls.start()  # one dispatcher thread, 1 new call per second
    escalation.scheduler.add_job(workers.check, "interval", seconds=10, id="worker-watchdog", replace_existing=True)
    if settings.workers_autostart:
        log.info("started %d camera workers", workers.start_enabled())
    log.info("%s API started: calls=%s whatsapp=%s", PRODUCT_NAME, calls.mode,
             escalation.whatsapp.mode)
    yield
    workers.stop_all()
    calls.stop()
    escalation.shutdown()


app = FastAPI(title=PRODUCT_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


PUBLIC_PATHS = ("/twilio/", "/share/")


@app.middleware("http")
async def public_address_guard(request: Request, call_next):
    """ngrok puts this API on the internet so Twilio can reach the webhooks. Requests that arrive through
    that public address may only use /twilio/* and /share/*; the dashboard API, /media and /docs stay local."""
    public_host = urlparse(get_settings().public_base_url).hostname
    host = (request.headers.get("host") or "").split(":")[0]
    if public_host and host == public_host and not request.url.path.startswith(PUBLIC_PATHS):
        return JSONResponse(status_code=404, content={"detail": "not found"})
    return await call_next(request)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """A bug must never leak a stack trace to the browser: log it, answer a plain 500."""
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "internal error; the details are in the API log"})


app.mount("/media", StaticFiles(directory=media_root()), name="media")

for module in (health, auth, users, cameras, stream, zones, events, stats, audit, responders, roll_call, broadcast,
               assistant, twilio, share, internal, invites, summary, sites, guards, badges, roles, duty, overview,
               activity, demo):
    app.include_router(module.router)
