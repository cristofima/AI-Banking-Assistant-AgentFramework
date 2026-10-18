"""ORM-only authentication, revocation, and atomic identity lifecycle operations."""
from __future__ import annotations

from datetime import timedelta

import jwt
from fastapi import HTTPException
from pydantic import ValidationError
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from sqlmodel import Session, select

from banking_shared.identity_models import (
    CustomerUser, IdentityAudit, Operator, Role, User, UserRole, utc_now,
)
from banking_shared import Customer
from identity.models.schemas import CustomerProfile, LoginResponse, OperatorCreate, UserProfile
from identity.services.oauth_signing import OAuthSigner
from identity.settings import Settings


def unauthorized() -> HTTPException:
    return HTTPException(401, detail={"code": "AUTH_INVALID_TOKEN"},
                         headers={"WWW-Authenticate": "Bearer"})


class IdentityService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.oauth_signer = OAuthSigner(settings)
        self.passwords = PasswordHash.recommended()
        self.dummy_hash = self.passwords.hash("unknown-account-timing-only")

    def profile(self, session: Session, user: User) -> UserProfile | CustomerProfile:
        assignment = session.get(UserRole, user.id)
        customer = session.get(CustomerUser, user.id)
        operator = session.get(Operator, user.id)
        role = session.get(Role, assignment.role_id) if assignment is not None else None
        if role is None or role.name not in {"customer", "operator", "admin"}:
            raise unauthorized()
        if role.name == "customer":
            if customer is None or operator is not None:
                raise unauthorized()
        elif (customer is not None or (role.name == "operator" and operator is None)
              or (role.name == "admin" and operator is not None)):
            raise unauthorized()
        common = dict(sub=user.id, email=user.email, locale=user.locale,
                      role=role.name, identity_version=user.identity_version,
                      name=(operator.display_name or user.name) if operator else user.name,
                      status=user.status, updated_at=user.updated_at)
        try:
            if customer is not None:
                return CustomerProfile(**common, customer_id=customer.customer_id)
            return UserProfile(**common)
        except ValidationError as exc:
            raise unauthorized() from exc

    def authenticate(self, session: Session, token: str, *, lock: bool = False) -> User:
        try:
            claims = jwt.decode(token, self.settings.jwt_secret_key.get_secret_value(),
                                algorithms=["HS256"], issuer=self.settings.jwt_issuer,
                                audience=self.settings.jwt_audience,
                                options={"require": ["sub", "email", "locale", "role",
                                                     "identity_version", "iss", "aud", "exp"]})
        except jwt.InvalidTokenError as exc:
            raise unauthorized() from exc
        if (not isinstance(claims["sub"], str)
                or type(claims["identity_version"]) is not int):
            raise unauthorized()
        statement = select(User).where(User.id == claims["sub"])
        if lock:
            statement = statement.with_for_update()
        user = session.exec(statement.execution_options(populate_existing=True)).first()
        if user is None or user.status != "active":
            raise unauthorized()
        profile = self.profile(session, user).model_dump(exclude_none=True)
        if any(claims.get(key) != profile.get(key)
               for key in ("email", "locale", "role", "identity_version", "customer_id")):
            raise unauthorized()
        if profile["role"] != "customer" and "customer_id" in claims:
            raise unauthorized()
        return user

    def admin(self, session: Session, token: str) -> User:
        user = self.authenticate(session, token, lock=True)
        if self.profile(session, user).role != "admin":
            raise HTTPException(403, detail={"code": "AUTH_ADMIN_REQUIRED"})
        return user

    def login(self, session: Session, email: str, password: str) -> LoginResponse:
        user = session.exec(select(User).where(User.email == email).with_for_update()).first()
        try:
            valid = self.passwords.verify(password, user.password_hash if user else self.dummy_hash)
        except (ValueError, TypeError, UnknownHashError):
            valid = False
        if user is None or not valid or user.status != "active":
            if user is not None:
                session.add(IdentityAudit(target_id=user.id, action="login", result="denied"))
                session.commit()
            raise HTTPException(401, detail={"code": "AUTH_INVALID_CREDENTIALS"},
                                headers={"WWW-Authenticate": "Bearer"})
        profile = self.profile(session, user)
        now = utc_now()
        expires = self.settings.access_token_minutes * 60
        claims = profile.model_dump(exclude_none=True, exclude={"status", "updated_at"})
        claims.update(iss=self.settings.jwt_issuer, aud=self.settings.jwt_audience,
                      iat=now, exp=now + timedelta(seconds=expires))
        token = jwt.encode(claims, self.settings.jwt_secret_key.get_secret_value(), algorithm="HS256")
        session.add(IdentityAudit(actor_id=user.id, target_id=user.id, action="login"))
        session.commit()
        return LoginResponse(access_token=token, expires_in=expires, user=profile)

    def create_operator(self, session: Session, token: str, request: OperatorCreate) -> UserProfile:
        actor = self.admin(session, token)
        if session.exec(select(User.id).where(User.email == request.email)).first():
            raise HTTPException(409, detail={"code": "AUTH_EMAIL_EXISTS"})
        user = User(email=request.email, password_hash=self.passwords.hash(
            request.password.get_secret_value()), locale=request.locale, name=request.name)
        session.add(user)
        session.flush()
        role = session.exec(select(Role).where(Role.name == "operator")).one()
        session.add(UserRole(user_id=user.id, role_id=role.id))
        session.add(Operator(user_id=user.id, first_name=request.first_name,
                             last_name=request.last_name))
        session.add(IdentityAudit(actor_id=actor.id, target_id=user.id, action="operator_create"))
        session.flush()
        profile = self.profile(session, user)
        session.commit()
        return profile

    def update_operator(self, session: Session, token: str, user_id: str,
                        action: str, password: str | None = None) -> UserProfile:
        actor = self.admin(session, token)
        user = session.exec(select(User).where(User.id == user_id).with_for_update()).first()
        if user is None or self.profile(session, user).role != "operator":
            raise HTTPException(404, detail={"code": "AUTH_OPERATOR_NOT_FOUND"})
        if action == "reset_password" and password is not None:
            user.password_hash = self.passwords.hash(password)
        elif action in {"activate", "deactivate"}:
            user.status = "active" if action == "activate" else "inactive"
        else:
            raise ValueError("Unsupported lifecycle action")
        user.identity_version += 1
        user.updated_at = utc_now()
        session.add(user)
        session.add(IdentityAudit(actor_id=actor.id, target_id=user.id,
                                  action=f"operator_{action}"))
        profile = self.profile(session, user)
        session.commit()
        return profile

    def customer_profile(self, session: Session, user: User) -> CustomerProfile:
        profile = self.profile(session, user)
        if (not isinstance(profile, CustomerProfile)
                or not profile.customer_id.strip()
                or session.get(Customer, profile.customer_id) is None):
            raise unauthorized()
        return profile

    def list_customers(self, session: Session, token: str) -> list[CustomerProfile]:
        self.admin(session, token)
        users = session.exec(select(User).join(UserRole).join(Role).where(
            Role.name == "customer").order_by(User.email)).all()
        return [self.customer_profile(session, user) for user in users]

    def update_customer(self, session: Session, token: str, user_id: str,
                        action: str) -> CustomerProfile:
        actor = self.admin(session, token)
        statement = select(User).where(User.id == user_id).with_for_update()
        user = session.exec(statement.execution_options(populate_existing=True)).first()
        try:
            if user is None:
                raise unauthorized()
            self.customer_profile(session, user)
        except HTTPException as exc:
            raise HTTPException(404, detail={"code": "AUTH_CUSTOMER_NOT_FOUND"}) from exc
        if action not in {"activate", "deactivate"}:
            raise ValueError("Unsupported lifecycle action")
        user.status = "active" if action == "activate" else "inactive"
        user.identity_version += 1
        user.updated_at = utc_now()
        session.add(user)
        session.add(IdentityAudit(actor_id=actor.id, target_id=user.id,
                                  action=f"customer_{action}"))
        profile = self.customer_profile(session, user)
        session.commit()
        return profile

    def list_operators(self, session: Session, token: str) -> list[UserProfile]:
        self.admin(session, token)
        users = session.exec(select(User).join(UserRole).join(Role).where(
            Role.name == "operator").order_by(User.email)).all()
        return [self.profile(session, user) for user in users]
