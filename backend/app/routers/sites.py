"""Sites, buildings, areas and rooms, and which camera watches which area.

GET  /sites                         (any logged-in user) the whole tree with cameras in each area
POST /sites                         (manage_sites) {name, address?}
POST /sites/{site_id}/buildings     (manage_sites) {name}
POST /buildings/{building_id}/areas (manage_sites) {name, kind: room|area}
PUT  /cameras/{camera_id}/area      (manage_sites) {area_id}   (null = not placed yet)
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.deps import current_user, require
from app.models import Area, Building, Camera, Site, User
from app.schemas import AreaCreate, AreaOut, BuildingCreate, BuildingOut, CameraAreaIn, SiteCreate, SiteOut, SitesOut
from app.services.audit import record

router = APIRouter(tags=["sites"])


@router.get("/sites", response_model=SitesOut)
def list_sites(_: User = Depends(current_user)) -> SitesOut:
    with SessionLocal() as db:
        cameras_by_area: dict[int, list[str]] = {}
        for cam in db.scalars(select(Camera).order_by(Camera.id)):
            if cam.area_id is not None:
                cameras_by_area.setdefault(cam.area_id, []).append(cam.id)
        sites = []
        for site in db.scalars(select(Site).order_by(Site.name)):
            buildings = []
            for building in db.scalars(select(Building).where(Building.site_id == site.id).order_by(Building.name)):
                areas = [AreaOut(id=a.id, name=a.name, kind=a.kind, camera_ids=cameras_by_area.get(a.id, []))
                         for a in db.scalars(select(Area).where(Area.building_id == building.id).order_by(Area.name))]
                buildings.append(BuildingOut(id=building.id, name=building.name, areas=areas))
            sites.append(SiteOut(id=site.id, name=site.name, address=site.address, buildings=buildings))
        return SitesOut(sites=sites)


@router.post("/sites", response_model=SiteOut, status_code=201)
def create_site(body: SiteCreate, admin: User = Depends(require("manage_sites"))) -> SiteOut:
    with SessionLocal() as db:
        site = Site(name=body.name, address=body.address)
        db.add(site)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="a site with that name already exists")
        record(db, admin, "site_create", target=f"site:{site.id}", detail={"name": site.name})
        db.commit()
        return SiteOut(id=site.id, name=site.name, address=site.address, buildings=[])


@router.post("/sites/{site_id}/buildings", response_model=BuildingOut, status_code=201)
def create_building(site_id: int, body: BuildingCreate, admin: User = Depends(require("manage_sites"))) -> BuildingOut:
    with SessionLocal() as db:
        if db.get(Site, site_id) is None:
            raise HTTPException(status_code=404, detail=f"site {site_id} not found")
        building = Building(site_id=site_id, name=body.name)
        db.add(building)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="that building already exists on this site")
        record(db, admin, "building_create", target=f"building:{building.id}", detail={"name": body.name})
        db.commit()
        return BuildingOut(id=building.id, name=building.name, areas=[])


@router.post("/buildings/{building_id}/areas", response_model=AreaOut, status_code=201)
def create_area(building_id: int, body: AreaCreate, admin: User = Depends(require("manage_sites"))) -> AreaOut:
    with SessionLocal() as db:
        if db.get(Building, building_id) is None:
            raise HTTPException(status_code=404, detail=f"building {building_id} not found")
        area = Area(building_id=building_id, name=body.name, kind=body.kind)
        db.add(area)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="that room or area already exists in this building")
        record(db, admin, "area_create", target=f"area:{area.id}", detail={"name": body.name, "kind": body.kind})
        db.commit()
        return AreaOut(id=area.id, name=area.name, kind=area.kind, camera_ids=[])


@router.put("/cameras/{camera_id}/area", response_model=AreaOut | None)
def place_camera(camera_id: str, body: CameraAreaIn, admin: User = Depends(require("manage_sites"))):
    with SessionLocal() as db:
        camera = db.get(Camera, camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail=f"camera {camera_id} not found")
        if body.area_id is None:
            camera.area_id = None
            record(db, admin, "camera_area", target=f"camera:{camera_id}", detail={"area_id": None})
            db.commit()
            return None
        area = db.get(Area, body.area_id)
        if area is None:
            raise HTTPException(status_code=422, detail=f"area {body.area_id} does not exist")
        camera.area_id = area.id
        record(db, admin, "camera_area", target=f"camera:{camera_id}", detail={"area_id": area.id})
        db.commit()
        return AreaOut(id=area.id, name=area.name, kind=area.kind, camera_ids=[camera_id])
