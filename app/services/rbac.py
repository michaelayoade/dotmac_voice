from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.rbac import Permission, PersonRole, Role, RolePermission
from app.schemas.rbac import (
    PermissionCreate,
    PermissionUpdate,
    PersonRoleCreate,
    PersonRoleUpdate,
    RoleCreate,
    RolePermissionCreate,
    RolePermissionUpdate,
    RoleUpdate,
)
from app.services.common import coerce_uuid
from app.services.exceptions import NotFoundError
from app.services.query_utils import apply_ordering, apply_pagination
from app.services.response import ListResponseMixin


class Roles(ListResponseMixin):
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, payload: RoleCreate):
        role = Role(**payload.model_dump())
        self.db.add(role)
        self.db.flush()
        self.db.refresh(role)
        return role

    def create_with_permissions(
        self, payload: RoleCreate, permission_ids: Sequence[str]
    ) -> Role:
        role = self.create(payload)
        self.replace_permissions(role.id, permission_ids)
        return role

    def get(self, role_id: str):
        role = self.db.get(Role, coerce_uuid(role_id))
        if not role:
            raise NotFoundError("Role not found")
        return role

    def list(
        self,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = select(Role)
        if is_active is None:
            query = query.where(Role.is_active.is_(True))
        else:
            query = query.where(Role.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Role.created_at, "name": Role.name},
        )
        return list(self.db.scalars(apply_pagination(query, limit, offset)).all())

    def update(self, role_id: str, payload: RoleUpdate):
        role = self.db.get(Role, coerce_uuid(role_id))
        if not role:
            raise NotFoundError("Role not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(role, key, value)
        self.db.flush()
        self.db.refresh(role)
        return role

    def update_with_permissions(
        self, role_id: str, payload: RoleUpdate, permission_ids: Sequence[str]
    ) -> Role:
        role = self.update(role_id, payload)
        self.replace_permissions(role.id, permission_ids)
        return role

    def replace_permissions(
        self, role_id: object, permission_ids: Sequence[str]
    ) -> None:
        role_uuid = coerce_uuid(str(role_id))
        existing = list(
            self.db.scalars(
                select(RolePermission).where(RolePermission.role_id == role_uuid)
            ).all()
        )
        for link in existing:
            self.db.delete(link)
        self.db.flush()
        role_permissions = RolePermissions(self.db)
        for permission_id in permission_ids:
            permission_uuid = coerce_uuid(permission_id)
            if permission_uuid is None:
                continue
            role_permissions.create(
                RolePermissionCreate(
                    role_id=role_uuid,
                    permission_id=permission_uuid,
                )
            )

    def delete(self, role_id: str):
        role = self.db.get(Role, coerce_uuid(role_id))
        if not role:
            raise NotFoundError("Role not found")
        role.is_active = False
        self.db.flush()


class Permissions(ListResponseMixin):
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, payload: PermissionCreate):
        permission = Permission(**payload.model_dump())
        self.db.add(permission)
        self.db.flush()
        self.db.refresh(permission)
        return permission

    def get(self, permission_id: str):
        permission = self.db.get(Permission, coerce_uuid(permission_id))
        if not permission:
            raise NotFoundError("Permission not found")
        return permission

    def list(
        self,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = select(Permission)
        if is_active is None:
            query = query.where(Permission.is_active.is_(True))
        else:
            query = query.where(Permission.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": Permission.created_at, "key": Permission.key},
        )
        return list(self.db.scalars(apply_pagination(query, limit, offset)).all())

    def update(self, permission_id: str, payload: PermissionUpdate):
        permission = self.db.get(Permission, coerce_uuid(permission_id))
        if not permission:
            raise NotFoundError("Permission not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(permission, key, value)
        self.db.flush()
        self.db.refresh(permission)
        return permission

    def delete(self, permission_id: str):
        permission = self.db.get(Permission, coerce_uuid(permission_id))
        if not permission:
            raise NotFoundError("Permission not found")
        permission.is_active = False
        self.db.flush()


class RolePermissions(ListResponseMixin):
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, payload: RolePermissionCreate):
        role = self.db.get(Role, coerce_uuid(payload.role_id))
        if not role:
            raise NotFoundError("Role not found")
        permission = self.db.get(Permission, coerce_uuid(payload.permission_id))
        if not permission:
            raise NotFoundError("Permission not found")
        link = RolePermission(**payload.model_dump())
        self.db.add(link)
        self.db.flush()
        self.db.refresh(link)
        return link

    def get(self, link_id: str):
        link = self.db.get(RolePermission, coerce_uuid(link_id))
        if not link:
            raise NotFoundError("Role permission not found")
        return link

    def list(
        self,
        role_id: str | None,
        permission_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = select(RolePermission)
        if role_id:
            query = query.where(RolePermission.role_id == coerce_uuid(role_id))
        if permission_id:
            query = query.where(
                RolePermission.permission_id == coerce_uuid(permission_id)
            )
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"role_id": RolePermission.role_id},
        )
        return list(self.db.scalars(apply_pagination(query, limit, offset)).all())

    def update(self, link_id: str, payload: RolePermissionUpdate):
        link = self.db.get(RolePermission, coerce_uuid(link_id))
        if not link:
            raise NotFoundError("Role permission not found")
        data = payload.model_dump(exclude_unset=True)
        if "role_id" in data:
            role = self.db.get(Role, data["role_id"])
            if not role:
                raise NotFoundError("Role not found")
        if "permission_id" in data:
            permission = self.db.get(Permission, data["permission_id"])
            if not permission:
                raise NotFoundError("Permission not found")
        for key, value in data.items():
            setattr(link, key, value)
        self.db.flush()
        self.db.refresh(link)
        return link

    def delete(self, link_id: str):
        link = self.db.get(RolePermission, coerce_uuid(link_id))
        if not link:
            raise NotFoundError("Role permission not found")
        self.db.delete(link)
        self.db.flush()


class PersonRoles(ListResponseMixin):
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, payload: PersonRoleCreate):
        person = self.db.get(Person, coerce_uuid(payload.person_id))
        if not person:
            raise NotFoundError("Person not found")
        role = self.db.get(Role, coerce_uuid(payload.role_id))
        if not role:
            raise NotFoundError("Role not found")
        link = PersonRole(**payload.model_dump())
        self.db.add(link)
        self.db.flush()
        self.db.refresh(link)
        return link

    def get(self, link_id: str):
        link = self.db.get(PersonRole, coerce_uuid(link_id))
        if not link:
            raise NotFoundError("Person role not found")
        return link

    def list(
        self,
        person_id: str | None,
        role_id: str | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = select(PersonRole)
        if person_id:
            query = query.where(PersonRole.person_id == coerce_uuid(person_id))
        if role_id:
            query = query.where(PersonRole.role_id == coerce_uuid(role_id))
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"assigned_at": PersonRole.assigned_at},
        )
        return list(self.db.scalars(apply_pagination(query, limit, offset)).all())

    def update(self, link_id: str, payload: PersonRoleUpdate):
        link = self.db.get(PersonRole, coerce_uuid(link_id))
        if not link:
            raise NotFoundError("Person role not found")
        data = payload.model_dump(exclude_unset=True)
        if "person_id" in data:
            person = self.db.get(Person, data["person_id"])
            if not person:
                raise NotFoundError("Person not found")
        if "role_id" in data:
            role = self.db.get(Role, data["role_id"])
            if not role:
                raise NotFoundError("Role not found")
        for key, value in data.items():
            setattr(link, key, value)
        self.db.flush()
        self.db.refresh(link)
        return link

    def delete(self, link_id: str):
        link = self.db.get(PersonRole, coerce_uuid(link_id))
        if not link:
            raise NotFoundError("Person role not found")
        self.db.delete(link)
        self.db.flush()
