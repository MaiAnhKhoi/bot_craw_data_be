from __future__ import annotations

from sqlalchemy import func, select
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
        return int(self.db.execute(select(func.count(User.id))).scalar_one())

    def by_id(self, user_id: int) -> User | None:
        return self.db.get(User, user_id)

    def page(self, params) -> tuple[list[User], int]:  # noqa: ANN001 — PageParams
        """Danh sách tài khoản, quản trị trước rồi tới người mới tạo sau cùng."""
        tong = int(self.db.execute(select(func.count(User.id))).scalar_one())
        rows = list(
            self.db.execute(
                select(User)
                .order_by(User.role.desc(), User.id)
                .offset(params.offset)
                .limit(params.size)
            ).scalars().all()
        )
        return rows, tong

    def dem_admin_dang_hoat_dong(self, tru_id: int | None = None) -> int:
        """Số quản trị còn dùng được, có thể trừ ra một người.

        Dùng để chặn nước đi tự khoá cửa: hạ vai trò hoặc khoá tài khoản quản
        trị CUỐI CÙNG thì không còn ai tạo được tài khoản, không còn ai đặt được
        lệnh quét — và cũng không còn ai sửa lại được chuyện đó từ trong giao
        diện.
        """
        dk = [User.role == "admin", User.is_active.is_(True)]
        if tru_id is not None:
            dk.append(User.id != tru_id)
        return int(self.db.execute(select(func.count(User.id)).where(*dk)).scalar_one())
