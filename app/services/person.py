from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.person import Person, PersonStatus
from app.schemas.person import PersonCreate, PersonUpdate
from app.services.common import coerce_uuid
from app.services.exceptions import NotFoundError
from app.services.query_utils import apply_ordering, apply_pagination, validate_enum
from app.services.response import ListResponseMixin, list_response


class People(ListResponseMixin):
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, payload: PersonCreate):
        person = Person(**payload.model_dump())
        self.db.add(person)
        self.db.flush()
        return person

    def get(self, person_id: str):
        person = self.db.get(Person, coerce_uuid(person_id))
        if not person:
            raise NotFoundError("Person not found")
        return person

    def list(
        self,
        email: str | None,
        status: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = select(Person)
        if email:
            query = query.where(Person.email.ilike(f"%{email}%"))
        if status:
            query = query.where(
                Person.status == validate_enum(status, PersonStatus, "status")
            )
        if is_active is not None:
            query = query.where(Person.is_active == is_active)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "created_at": Person.created_at,
                "last_name": Person.last_name,
                "email": Person.email,
            },
        )
        return list(self.db.scalars(apply_pagination(query, limit, offset)).all())

    def list_response(
        self,
        email: str | None,
        status: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        items = self.list(email, status, is_active, order_by, order_dir, limit, offset)
        return cast(dict[str, Any], list_response(items, limit, offset))

    def update(self, person_id: str, payload: PersonUpdate):
        person = self.db.get(Person, coerce_uuid(person_id))
        if not person:
            raise NotFoundError("Person not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(person, key, value)
        self.db.flush()
        return person

    def delete(self, person_id: str):
        person = self.db.get(Person, coerce_uuid(person_id))
        if not person:
            raise NotFoundError("Person not found")
        self.db.delete(person)
        self.db.flush()
