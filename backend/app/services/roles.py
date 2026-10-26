"""P22. Role templates: an admin creates a role and gives it cameras and actions.

A user's permissions stay on the user row (code only ever reads user.camera_ids and user.actions).
A role is the template that fills them:
  - a new user or invitation with this role and no camera/action list gets the role's lists;
  - when the role's lists change, the difference is applied to every user with that role
    (added items are added, removed items are removed, each user's own extras stay).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import Role, User


def find(db: DbSession, name: str) -> Role | None:
    return db.scalar(select(Role).where(Role.name == name))


def defaults(db: DbSession, role_name: str, camera_ids: list[str] | None,
             actions: list[str] | None) -> tuple[list[str], list[str]]:
    """Fill missing lists from the role (or empty when the role is not defined)."""
    role = find(db, role_name)
    cams = camera_ids if camera_ids is not None else (list(role.camera_ids) if role else [])
    acts = actions if actions is not None else (list(role.actions) if role else [])
    return sorted(set(cams)), sorted(set(acts))


def apply_change(db: DbSession, role_name: str, old_cameras: list[str], new_cameras: list[str],
                 old_actions: list[str], new_actions: list[str]) -> list[User]:
    """Push a role edit to its users. Returns the users that were changed."""
    changed = []
    for user in db.scalars(select(User).where(User.role == role_name)):
        cams = (set(user.camera_ids) - (set(old_cameras) - set(new_cameras))) | set(new_cameras)
        acts = (set(user.actions) - (set(old_actions) - set(new_actions))) | set(new_actions)
        if cams != set(user.camera_ids) or acts != set(user.actions):
            user.camera_ids, user.actions = sorted(cams), sorted(acts)
            changed.append(user)
    return changed


def add_camera(db: DbSession, camera_id: str, role_names: list[str]) -> int:
    """A new camera given to roles: add it to each role and to every user with that role."""
    count = 0
    for name in role_names:
        role = find(db, name)
        if role is not None and camera_id not in role.camera_ids:
            role.camera_ids = sorted({*role.camera_ids, camera_id})
        for user in db.scalars(select(User).where(User.role == name)):
            if camera_id not in user.camera_ids:
                user.camera_ids = sorted({*user.camera_ids, camera_id})
                count += 1
    return count
