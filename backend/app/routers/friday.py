"""The wake-word listener's way in (laptop microphone -> API). Header X-Internal-Key; acts as FRIDAY_USER.

POST /internal/friday/event          {state, heard?, answer?}: tell every dashboard what Friday is doing.
                                      Returns {dashboards_open, friday_user_online}: when the user is not on the
                                      software, the listener opens the browser on Friday's page.
POST /internal/friday/voice-command  audio -> {transcript, intent, status, answer, confirm_token, ms, data,
                                      spoken_by}. spoken_by "dashboard": FRIDAY_USER's open dashboard speaks the
                                      answer (the blob moves with it); "listener": nobody has it open, so the
                                      listener plays it on the laptop speaker (from /internal/friday/speak).
POST /internal/friday/confirm        {confirm_token, confirmed}: the spoken "yes" / "no" for a big action.
POST /internal/friday/speak          {text} -> audio/wav in Friday's voice (Rumik).
"""

import re
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import User
from app.routers.assistant import SORRY, _log, _push, _read_audio
from app.routers.internal import require_internal_key
from app.schemas import ConfirmIn, ConfirmOut, SpeakIn, VoiceCommandOut
from app.services import commands, tts
from app.services.stt import SttUnavailable, transcribe
from app.ws import manager

YES = re.compile(r"^\W*(yes|yeah|yep|yup|confirm|do it|go ahead)\b", re.I)

router = APIRouter(prefix="/internal/friday", tags=["friday"], dependencies=[Depends(require_internal_key)])


class FridayEventIn(BaseModel):
    state: str = "listening"  # listening, thinking, idle
    heard: str | None = None
    answer: str | None = None


class FridayEventOut(BaseModel):
    dashboards_open: int
    friday_user_online: bool


class ListenerCommandOut(VoiceCommandOut):
    spoken_by: str  # dashboard or listener


def friday_user() -> User:
    name = get_settings().friday_user
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.name == name, User.active.is_(True))) if name else None
        if user is None:
            raise HTTPException(status_code=503, detail="set FRIDAY_USER in .env to the name of an active user")
        db.expunge(user)
        return user


def _notice(state: str, heard: str | None = None, answer: str | None = None, speak_for: int | None = None,
            camera_ids: list[str] | None = None) -> dict:
    """camera_ids: "show cameras 3 and 4" -> the dashboard opens the Live page with them at once."""
    return {"kind": "friday", "state": state, "heard": heard, "answer": answer, "speak_for_user_id": speak_for,
            "camera_ids": camera_ids}


def _cameras(result) -> list[str] | None:
    ids = result.data.get("camera_ids") if result is not None and result.intent == "show_camera" else None
    return list(ids) if ids else None


@router.post("/event", response_model=FridayEventOut)
async def event(body: FridayEventIn) -> FridayEventOut:
    online = manager.online_user_ids()
    name = get_settings().friday_user
    user_online = False
    if name:
        with SessionLocal() as db:
            user_id = db.scalar(select(User.id).where(User.name == name))
        user_online = user_id in online
    await manager.broadcast(_notice(body.state, body.heard, body.answer))
    return FridayEventOut(dashboards_open=len(online), friday_user_online=user_online)


@router.post("/voice-command", response_model=ListenerCommandOut)
async def voice_command(audio: UploadFile = File(...), confirm_token: str | None = Form(None)) -> ListenerCommandOut:
    """With confirm_token: this recording is the answer to "say yes to confirm". Only a clear yes confirms;
    anything else (no, silence, mumbling) cancels the big action."""
    t0 = time.perf_counter()
    user = await run_in_threadpool(friday_user)
    data = await _read_audio(audio)
    await manager.broadcast(_notice("thinking"))
    try:
        transcript, _ = await run_in_threadpool(transcribe, data, audio.filename or "speech.wav",
                                                (audio.content_type or "").lower())
    except SttUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"speech to text is not available: {exc}")
    transcript = transcript.strip()
    on_screen = user.id in manager.online_user_ids()
    spoken_by = "dashboard" if on_screen else "listener"
    if confirm_token:
        yes = bool(YES.search(transcript))
        result = await run_in_threadpool(commands.confirm, confirm_token, yes, user)
        answer = result.answer if result else "That confirmation expired. Nothing was done."
        await run_in_threadpool(_log, user, "assistant_command", "voice", {
            "text": transcript or "(silence)", "intent": result.intent if result else "confirm",
            "status": result.status if result else "refused", "answer": answer, "via": "wake word"})
        if result:
            await _push(result.notices)
        await manager.broadcast(_notice("speaking", transcript, answer, user.id if on_screen else None))
        return ListenerCommandOut(transcript=transcript, intent=result.intent if result else "confirm",
                                  status=result.status if result else "refused", answer=answer, confirm_token=None,
                                  ms=int((time.perf_counter() - t0) * 1000), data=result.data if result else {},
                                  spoken_by=spoken_by)
    if not transcript:
        await manager.broadcast(_notice("speaking", "", SORRY, user.id if on_screen else None))
        return ListenerCommandOut(transcript="", intent="unknown", status="unknown", answer=SORRY, confirm_token=None,
                                  ms=int((time.perf_counter() - t0) * 1000), data={}, spoken_by=spoken_by)
    result = await run_in_threadpool(commands.handle, transcript, user)
    await run_in_threadpool(_log, user, "assistant_command", "voice", {
        "text": transcript, "intent": result.intent, "status": result.status, "answer": result.answer,
        "source": result.data.get("source"), "via": "wake word"})
    await _push(result.notices)
    await manager.broadcast(_notice("speaking", transcript, result.answer, user.id if on_screen else None,
                                    _cameras(result)))
    return ListenerCommandOut(transcript=transcript, intent=result.intent, status=result.status, answer=result.answer,
                              confirm_token=result.confirm_token, ms=int((time.perf_counter() - t0) * 1000),
                              data=result.data, spoken_by=spoken_by)


@router.post("/confirm", response_model=ConfirmOut)
async def confirm(body: ConfirmIn) -> ConfirmOut:
    t0 = time.perf_counter()
    user = await run_in_threadpool(friday_user)
    result = await run_in_threadpool(commands.confirm, body.confirm_token, body.confirmed, user)
    if result is None:
        raise HTTPException(status_code=404, detail="nothing to confirm (expired, already used, or not yours)")
    await run_in_threadpool(_log, user, "assistant_command", "voice", {
        "text": "yes" if body.confirmed else "no", "intent": result.intent, "status": result.status,
        "answer": result.answer, "via": "wake word"})
    await _push(result.notices)
    on_screen = user.id in manager.online_user_ids()
    await manager.broadcast(_notice("speaking", "yes" if body.confirmed else "no", result.answer,
                                    user.id if on_screen else None))
    return ConfirmOut(status="done" if result.status == "done" else "refused", answer=result.answer,
                      ms=int((time.perf_counter() - t0) * 1000), data=result.data)


@router.post("/speak", responses={200: {"content": {"audio/wav": {}}}})
async def speak(body: SpeakIn) -> Response:
    try:
        audio, _ms = await run_in_threadpool(tts.speak, body.text)
    except tts.TtsUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"text to speech is not available: {exc}")
    return Response(content=audio, media_type="audio/wav")
