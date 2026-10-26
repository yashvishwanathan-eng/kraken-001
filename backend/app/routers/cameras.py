"""Cameras the caller may see, with live status, zones and a signed stream URL.

P22, adding and installing cameras (manage_cameras):
GET  /cameras/admin               every camera with its address, port and worker state
POST /cameras/test                {source}: open the address and read one frame (nothing is saved)
POST /cameras                     add a camera: tested first, saved, given to roles, its worker started
PUT  /cameras/{id}                rename, renumber, change the address, enable or disable
POST /cameras/{id}/start|stop|restart   the camera's worker process
GET  /cameras/{id}/worker-log     the last lines the worker printed
Cameras are never deleted from here (that would delete their incidents); disable them instead.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.deps import current_user, require
from app.models import Area, Camera, User, Zone
from app.schemas import (
    CameraAdminOut, CameraCreate, CameraOut, CameraTestIn, CameraTestOut, CameraUpdate, StreamUrlResponse,
    WorkerLogOut, WorkerStatus, ZoneOut,
)
from app.services import camera_registry, camera_status, roles
from app.services.audit import record
from app.services.permissions import allowed_cameras, can_see, stream_url
from app.services.workers import workers

router = APIRouter(prefix="/cameras", tags=["cameras"])


def zone_out(zone: Zone) -> ZoneOut:
    return ZoneOut(id=zone.id, camera_id=zone.camera_id, name=zone.name, kind=zone.kind,
                   polygon=[tuple(p) for p in zone.polygon], max_people=zone.max_people)


@router.get("", response_model=list[CameraOut])
def list_cameras(user: User = Depends(current_user)) -> list[CameraOut]:
    scope = allowed_cameras(user)
    with SessionLocal() as db:
        query = select(Camera).options(selectinload(Camera.zones)).order_by(Camera.number.nulls_last(), Camera.id)
        if scope is not None:
            query = query.where(Camera.id.in_(scope))
        result = []
        for camera in db.scalars(query):
            online, fps = camera_status.get(camera.id)
            result.append(CameraOut(
                id=camera.id, number=camera.number, name=camera.name, stream_url=stream_url(user.id, camera.id),
                online=online, fps=fps,
                zones=[zone_out(z) for z in sorted(camera.zones, key=lambda z: z.id)],
            ))
        return result


@router.get("/{camera_id}/stream-url", response_model=StreamUrlResponse)
def refresh_stream_url(camera_id: str, user: User = Depends(current_user)) -> StreamUrlResponse:
    with SessionLocal() as db:
        if db.get(Camera, camera_id) is None:
            raise HTTPException(status_code=404, detail=f"camera {camera_id} not found")
    if not can_see(user, camera_id):
        raise HTTPException(status_code=403, detail=f"camera {camera_id} is not delegated to you")
    return StreamUrlResponse(stream_url=stream_url(user.id, camera_id))


# ---------- P22: add, test and install cameras ----------

def admin_out(camera: Camera) -> CameraAdminOut:
    online, fps = camera_status.get(camera.id)
    return CameraAdminOut(id=camera.id, number=camera.number, name=camera.name, source=camera.source,
                          port=camera.port, enabled=camera.enabled, area_id=camera.area_id, added_by=camera.added_by,
                          online=online, fps=fps, worker=WorkerStatus(**workers.status(camera.id)))


def _load(db, camera_id: str) -> Camera:
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail=f"camera {camera_id} not found")
    return camera


def _check_number(db, number: int | None, camera_id: str | None) -> None:
    if number is None:
        return
    other = db.scalar(select(Camera).where(Camera.number == number))
    if other is not None and other.id != camera_id:
        raise HTTPException(status_code=409, detail=f"camera number {number} is already {other.name}")


@router.get("/admin", response_model=list[CameraAdminOut])
def admin_list(_: User = Depends(require("manage_cameras"))) -> list[CameraAdminOut]:
    with SessionLocal() as db:
        return [admin_out(c) for c in db.scalars(select(Camera).order_by(Camera.number.nulls_last(), Camera.id))]


@router.post("/test", response_model=CameraTestOut)
async def test_camera(body: CameraTestIn, _: User = Depends(require("manage_cameras"))) -> CameraTestOut:
    result = await run_in_threadpool(camera_registry.probe, body.source)
    return CameraTestOut(**result.__dict__)


@router.post("", response_model=CameraAdminOut, status_code=201)
async def add_camera(body: CameraCreate, admin: User = Depends(require("manage_cameras"))) -> CameraAdminOut:
    if body.test_first:
        probe = await run_in_threadpool(camera_registry.probe, body.source)
        if not probe.ok:
            raise HTTPException(status_code=422, detail=f"camera test failed: {probe.error}. Fix the address, "
                                                        "or send test_first=false to save it anyway")
    return await run_in_threadpool(_save_new, body, admin)


def _save_new(body: CameraCreate, admin: User) -> CameraAdminOut:
    with SessionLocal() as db:
        camera_id = body.id or camera_registry.slug(body.name)
        if db.get(Camera, camera_id) is not None:
            raise HTTPException(status_code=409, detail=f"camera id {camera_id} already exists; pick another id")
        _check_number(db, body.number, None)
        if body.area_id is not None and db.get(Area, body.area_id) is None:
            raise HTTPException(status_code=422, detail=f"area {body.area_id} does not exist")
        unknown = [name for name in body.role_names if roles.find(db, name) is None and
                   db.scalar(select(User.id).where(User.role == name).limit(1)) is None]
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown roles: {', '.join(unknown)}")
        camera = Camera(id=camera_id, name=body.name, source=body.source, port=camera_registry.next_port(db),
                        number=body.number or camera_registry.next_number(db), area_id=body.area_id,
                        enabled=True, added_by=admin.name)
        db.add(camera)
        db.flush()
        given = roles.add_camera(db, camera_id, body.role_names)
        record(db, admin, "camera_add", target=f"camera:{camera_id}",
               detail={"name": body.name, "number": camera.number, "roles": body.role_names, "users_given": given})
        db.commit()
        if body.start:
            workers.start(camera_id)
        return admin_out(camera)


@router.put("/{camera_id}", response_model=CameraAdminOut)
def update_camera(camera_id: str, body: CameraUpdate,
                  admin: User = Depends(require("manage_cameras"))) -> CameraAdminOut:
    with SessionLocal() as db:
        camera = _load(db, camera_id)
        _check_number(db, body.number, camera_id)
        changes = body.model_dump(exclude_none=True)
        for field, value in changes.items():
            setattr(camera, field, value)
        record(db, admin, "camera_update", target=f"camera:{camera_id}", detail=changes)
        db.commit()
        running = workers.status(camera_id)["running"]
        if body.enabled is False:
            workers.stop(camera_id)
        elif running and "source" in changes:
            workers.restart(camera_id)  # the worker reads its address at start
        return admin_out(camera)


@router.post("/{camera_id}/{op}", response_model=CameraAdminOut)
def worker_op(camera_id: str, op: str, admin: User = Depends(require("manage_cameras"))) -> CameraAdminOut:
    if op not in ("start", "stop", "restart"):
        raise HTTPException(status_code=404, detail="use start, stop or restart")
    with SessionLocal() as db:
        camera = _load(db, camera_id)
        if op != "stop" and not camera.enabled:
            raise HTTPException(status_code=409, detail=f"camera {camera_id} is disabled; enable it first")
        {"start": workers.start, "stop": workers.stop, "restart": workers.restart}[op](camera_id)
        record(db, admin, f"camera_{op}", target=f"camera:{camera_id}")
        db.commit()
        return admin_out(camera)


@router.get("/{camera_id}/worker-log", response_model=WorkerLogOut)
def worker_log(camera_id: str, _: User = Depends(require("manage_cameras"))) -> WorkerLogOut:
    with SessionLocal() as db:
        _load(db, camera_id)
    return WorkerLogOut(camera_id=camera_id, lines=workers.log_tail(camera_id))
