import argparse

from dotenv import load_dotenv

from app.db import SessionLocal
from app.models.person import Person
from app.models.rbac import Permission, PersonRole, Role, RolePermission

DEFAULT_PERMISSIONS = [
    ("audit:read", "Read audit events"),
    ("auth:manage", "Manage authentication"),
    ("billing:read", "Read billing data"),
    ("billing:manage", "Manage billing configuration"),
    ("people:read", "Read people profiles"),
    ("people:write", "Manage people profiles"),
    ("rbac:manage", "Manage roles and permissions"),
    ("scheduler:manage", "Manage scheduled tasks"),
    ("settings:manage", "Manage application settings"),
    ("subscriptions:read", "Read subscriptions"),
    ("subscriptions:manage", "Manage subscriptions"),
]

DEFAULT_ROLES = [
    ("admin", "Full system access"),
    ("auditor", "Audit read-only access"),
    ("operator", "Settings and scheduler operations"),
    ("support", "People and account support"),
]

ROLE_PERMISSIONS = {
    "admin": [perm for perm, _ in DEFAULT_PERMISSIONS],
    "auditor": ["audit:read"],
    "operator": ["scheduler:manage", "settings:manage"],
    "support": ["people:read"],
}


def parse_args():
    parser = argparse.ArgumentParser(description="Seed RBAC roles and permissions.")
    parser.add_argument("--admin-email", help="Email to map to admin role.")
    parser.add_argument("--admin-person-id", help="Person ID to map to admin role.")
    return parser.parse_args()


def _ensure_role(db, name, description):
    role = db.query(Role).filter(Role.name == name).first()
    if not role:
        role = Role(name=name, description=description, is_active=True)
        db.add(role)
    else:
        if not role.is_active:
            role.is_active = True
        if description and not role.description:
            role.description = description
    return role


def _ensure_permission(db, key, description):
    permission = db.query(Permission).filter(Permission.key == key).first()
    if not permission:
        permission = Permission(key=key, description=description, is_active=True)
        db.add(permission)
    else:
        if not permission.is_active:
            permission.is_active = True
        if description and not permission.description:
            permission.description = description
    return permission


def _ensure_role_permission(db, role_id, permission_id):
    link = (
        db.query(RolePermission)
        .filter(RolePermission.role_id == role_id)
        .filter(RolePermission.permission_id == permission_id)
        .first()
    )
    if not link:
        link = RolePermission(role_id=role_id, permission_id=permission_id)
        db.add(link)
    return link


def _ensure_person_role(db, person_id, role_id):
    link = (
        db.query(PersonRole)
        .filter(PersonRole.person_id == person_id)
        .filter(PersonRole.role_id == role_id)
        .first()
    )
    if not link:
        link = PersonRole(person_id=person_id, role_id=role_id)
        db.add(link)
    return link


def main():
    load_dotenv()
    args = parse_args()
    db = SessionLocal()
    try:
        for name, description in DEFAULT_ROLES:
            _ensure_role(db, name, description)
        for key, description in DEFAULT_PERMISSIONS:
            _ensure_permission(db, key, description)
        db.commit()

        roles = {role.name: role for role in db.query(Role).all()}
        permissions = {perm.key: perm for perm in db.query(Permission).all()}
        for role_name, permission_keys in ROLE_PERMISSIONS.items():
            role = roles.get(role_name)
            if not role:
                continue
            for key in permission_keys:
                permission = permissions.get(key)
                if not permission:
                    continue
                _ensure_role_permission(db, role.id, permission.id)
        db.commit()

        admin_role = roles.get("admin")
        if admin_role and (args.admin_email or args.admin_person_id):
            person = None
            if args.admin_person_id:
                person = db.get(Person, args.admin_person_id)
            if not person and args.admin_email:
                person = (
                    db.query(Person).filter(Person.email == args.admin_email).first()
                )
            if not person:
                raise SystemExit("Admin person not found.")
            _ensure_person_role(db, person.id, admin_role.id)
            db.commit()
            print("Admin role assigned.")
        print("RBAC seed complete.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
