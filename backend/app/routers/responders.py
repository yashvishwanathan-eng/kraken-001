"""Responders: the people the escalation ladder phones. Reading needs a login; changes need manage_responders."""

from datetime import time

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select

from app.db import SessionLocal
from app.deps import current_user, require
from app.models import Responder, User
from app.schemas import ResponderIn, ResponderOut
from app.services.audit import record

router = APIRouter(prefix="/responders", tags=["responders"])


def _hhmm(value: time) -> str:
    return value.strftime("%H:%M")


def _time(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def to_out(r: Responder) -> ResponderOut:
    return ResponderOut(id=r.id, name=r.name, phone=r.phone, language=r.language, zone=r.zone,
                        shift_start=_hhmm(r.shift_start), shift_end=_hhmm(r.shift_end),
                        call_order=r.call_order, active=r.active)


def _apply(r: Responder, body: ResponderIn) -> None:
    r.name, r.phone, r.language, r.zone = body.name, body.phone, body.language, body.zone
    r.shift_start, r.shift_end = _time(body.shift_start), _time(body.shift_end)
    r.call_order, r.active = body.call_order, body.active


@router.get("", response_model=list[ResponderOut])
def list_responders(_: User = Depends(current_user)) -> list[ResponderOut]:
    with SessionLocal() as db:
        return [to_out(r) for r in db.scalars(select(Responder).order_by(Responder.zone, Responder.call_order))]


@router.post("", response_model=ResponderOut, status_code=201)
def create_responder(body: ResponderIn, user: User = Depends(require("manage_responders"))) -> ResponderOut:
    with SessionLocal() as db:
        responder = Responder()
        _apply(responder, body)
        db.add(responder)
        db.flush()
        record(db, user, "responder_create", target=f"responder:{responder.id}", detail={"name": responder.name})
        db.commit()
        return to_out(responder)


@router.put("/{responder_id}", response_model=ResponderOut)
def update_responder(responder_id: int, body: ResponderIn,
                     user: User = Depends(require("manage_responders"))) -> ResponderOut:
    with SessionLocal() as db:
        responder = db.get(Responder, responder_id)
        if responder is None:
            raise HTTPException(status_code=404, detail=f"responder {responder_id} not found")
        _apply(responder, body)
        record(db, user, "responder_update", target=f"responder:{responder.id}", detail={"name": responder.name})
        db.commit()
        return to_out(responder)


@router.delete("/{responder_id}", status_code=204)
def delete_responder(responder_id: int, user: User = Depends(require("manage_responders"))) -> Response:
    with SessionLocal() as db:
        responder = db.get(Responder, responder_id)
        if responder is None:
            raise HTTPException(status_code=404, detail=f"responder {responder_id} not found")
        record(db, user, "responder_delete", target=f"responder:{responder.id}", detail={"name": responder.name})
        db.delete(responder)
        db.commit()
    return Response(status_code=204)
