"""Tạo tài khoản quản trị đầu tiên (idempotent).

    python -m scripts.create_admin                 # lấy từ BCD_ADMIN_USERNAME / BCD_ADMIN_PASSWORD
    python -m scripts.create_admin ten matkhau     # hoặc truyền tay
"""
from __future__ import annotations

import sys

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.modules.identity.entity import User
from app.modules.identity.repository import UserRepository


def main() -> int:
    settings = get_settings()
    username = sys.argv[1] if len(sys.argv) > 2 else settings.admin_username
    password = sys.argv[2] if len(sys.argv) > 2 else settings.admin_password

    db = SessionLocal()
    try:
        repo = UserRepository(db)
        if repo.by_username(username):
            print(f"Tài khoản '{username}' đã tồn tại, bỏ qua.")
            return 0
        repo.add(User(username=username, password_hash=hash_password(password), full_name="Quản trị"))
        db.commit()
        print(f"Đã tạo tài khoản '{username}'. Hãy đổi mật khẩu mặc định nếu chưa đổi.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
