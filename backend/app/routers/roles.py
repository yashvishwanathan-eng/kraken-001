"""P22. Roles: the admin creates roles and gives them cameras and actions (manage_users).

GET    /roles          list roles with how many users have each (manage_users or delegate_cameras)
POST   /roles          {name, description?, camera_ids[], actions[]}
PUT    /roles/{id}     partial; camera and action changes are applied to every user with this role
DELETE /roles/{id}     the template only; users keep their role label and permissions
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select

from app.db import SessionLocal
from app.deps import require, require_any
from app.models import Role, User
from app.routers.users import _validate
from app.schemas import RoleIn, RoleOut, RoleUpdate
from app.services import roles
from app.services.audit import record

router = APIRouter(prefix="/roles", tags=["roles"])


def _out(db, role: Role) -> RoleOut:
    users = db.scalar(select(func.count(User.id)).where(User.role == role.name)) or 0
    return RoleOut(id=role.id, name=role.name, description=role.description, camera_ids=list(role.camera_ids),
                   actions=list(role.actions), users=users)


@router.get("", response_model=list[RoleOut])
def list_roles(_: User = Depends(require_any("manage_users", "delegate_cameras"))) -> list[RoleOut]:
    with SessionLocal() as db:
        return [_out(db, r) for r in db.scalars(select(Role).order_by(Role.name))]


@router.post("", response_model=RoleOut, status_code=201)
def create_role(body: RoleIn, admin: User = Depends(require("manage_users"))) -> RoleOut:
    with SessionLocal() as db:
        _validate(db, body.camera_ids, body.actions, None, None)
        if roles.find(db, body.name) is not None:
            raise HTTPException(status_code=409, detail=f"role {body.name} already exists")
        role = Role(name=body.name, description=body.description, camera_ids=sorted(set(body.camera_ids)),
                    actions=sorted(set(body.actions)))
        db.add(role)
        db.flush()
        record(db, admin, "role_create", target=f"role:{role.id}", detail={"name": role.name})
        db.commit()
        return _out(db, role)


@router.put("/{role_id}", response_model=RoleOut)
def update_role(role_id: int, body: RoleUpdate, admin: User = Depends(require("manage_users"))) -> RoleOut:
    with SessionLocal() as db:
        role = db.get(Role, role_id)
        if role is None:
            raise HTTPException(status_code=404, detail=f"role {role_id} not found")
        _validate(db, body.camera_ids, body.actions, None, None)
        if body.name is not None and body.name != role.name and roles.find(db, body.name) is not None:
            raise HTTPException(status_code=409, detail=f"role {body.name} already exists")
        if (body.actions is not None and role.name == admin.role and "manage_users" in role.actions
                and "manage_users" not in body.actions):
            raise HTTPException(status_code=400, detail="you cannot remove manage_users from your own role")
        old_name, old_cams, old_acts = role.name, list(role.camera_ids), list(role.actions)
        new_cams = sorted(set(body.camera_ids)) if body.camera_ids is not None else old_cams
        new_acts = sorted(set(body.actions)) if body.actions is not None else old_acts
        changed = roles.apply_change(db, old_name, old_cams, new_cams, old_acts, new_acts)
        if body.name is not None and body.name != old_name:
            for user in db.scalars(select(User).where(User.role == old_name)):
                user.role = body.name
            role.name = body.name
        if body.description is not None:
            role.description = body.description
        role.camera_ids, role.actions = new_cams, new_acts
        record(db, admin, "role_update", target=f"role:{role.id}",
               detail={**body.model_dump(exclude_none=True), "users_changed": len(changed)})
        db.commit()
        return _out(db, role)


@router.delete("/{role_id}", status_code=204)
def delete_role(role_id: int, admin: User = Depends(require("manage_users"))) -> Response:
    with SessionLocal() as db:
        role = db.get(Role, role_id)
        if role is None:
            raise HTTPException(status_code=404, detail=f"role {role_id} not found")
        record(db, admin, "role_delete", target=f"role:{role.id}", detail={"name": role.name})
        db.delete(role)
        db.commit()
    return Response(status_code=204)
