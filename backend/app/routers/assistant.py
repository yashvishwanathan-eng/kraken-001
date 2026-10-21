"""The AI assistant: questions about incidents (typed or spoken) and laptop commands."""

import time

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.config import get_settings
from app.db import SessionLocal
from app.deps import require
from app.models import User
from app.schemas import (
    AssistantAnswer, AssistantQueryIn, AssistantVoiceAnswer, CommandIn, CommandOut, ConfirmIn, ConfirmOut, IncidentOut,
)
from app.services import commands
from app.services.assistant import ask
from app.services.stt import SttUnavailable, transcribe
from app.ws import manager

router = APIRouter(prefix="/assistant", tags=["assistant"])

AUDIO_PREFIXES = ("audio/", "video/webm")  # browsers record push-to-talk as audio/webm (sometimes video/webm)


def _ask(question: str, user: User) -> AssistantAnswer:
    with SessionLocal() as db:
        answer, rows, source, ms = ask(db, question, user)
        return AssistantAnswer(answer=answer, incidents=[IncidentOut.from_model(r) for r in rows], source=source,
                               ms=ms)


@router.post("/query", response_model=AssistantAnswer)
def query(body: AssistantQueryIn, user: User = Depends(require("assistant"))) -> AssistantAnswer:
    return _ask(body.question, user)


@router.post("/voice", response_model=AssistantVoiceAnswer)
async def voice(audio: UploadFile = File(...), user: User = Depends(require("assistant"))) -> AssistantVoiceAnswer:
    t0 = time.perf_counter()
    content_type = (audio.content_type or "").lower()
    if not content_type.startswith(AUDIO_PREFIXES):
        raise HTTPException(status_code=422, detail="send an audio file (webm, wav, mp3, m4a, ogg)")
    limit = int(get_settings().voice_max_mb * 1024 * 1024)
    data = await audio.read(limit + 1)
    if not data or len(data) > limit:
        raise HTTPException(status_code=422, detail=f"audio must be between 1 byte and {get_settings().voice_max_mb} MB")
    try:
        transcript, _ = await run_in_threadpool(transcribe, data, audio.filename or "speech.webm", content_type)
    except SttUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"speech to text is not available: {exc}")
    if not transcript:
        raise HTTPException(status_code=422, detail="no speech heard in the recording")
    result = await run_in_threadpool(_ask, transcript, user)
    return AssistantVoiceAnswer(transcript=transcript, answer=result.answer, incidents=result.incidents,
                                source=result.source, ms=int((time.perf_counter() - t0) * 1000))


async def _push(notices: list[dict]) -> None:
    for notice in notices:
        await manager.broadcast(notice)


@router.post("/command", response_model=CommandOut)
async def command(body: CommandIn, user: User = Depends(require("assistant"))) -> CommandOut:
    t0 = time.perf_counter()
    result = await run_in_threadpool(commands.handle, body.text, user)
    await _push(result.notices)
    return CommandOut(intent=result.intent, status=result.status, answer=result.answer,
                      confirm_token=result.confirm_token, ms=int((time.perf_counter() - t0) * 1000), data=result.data)


@router.post("/confirm", response_model=ConfirmOut)
async def confirm(body: ConfirmIn, user: User = Depends(require("assistant"))) -> ConfirmOut:
    t0 = time.perf_counter()
    result = await run_in_threadpool(commands.confirm, body.confirm_token, body.confirmed, user)
    if result is None:
        raise HTTPException(status_code=404, detail="nothing to confirm (expired, already used, or not yours)")
    await _push(result.notices)
    return ConfirmOut(status="done" if result.status == "done" else "refused", answer=result.answer,
                      ms=int((time.perf_counter() - t0) * 1000), data=result.data)
