from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.entity import User


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def by_username(self, username: str) -> User | None:
        return self.db.execute(select(User).where(User.username == username)).scalar_one_or_none()

    def add(self, user: User) -> User:
        self.db.add(user)
        self.db.flush()
        return user

    def count(self) -> int:
        return int(self.db.execute(select(User.id)).scalars().all().__len__())
