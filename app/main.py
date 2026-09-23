"""Lắp ráp ứng dụng FastAPI. File này KHÔNG chứa logic nghiệp vụ."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.exceptions import register_exception_handlers
from app.core.logging_config import setup_logging
from app.core.request_context import RequestContextMiddleware
from app.core.response import ApiResponse
from app.modules.geo.router import router as geo_router
from app.modules.identity.router import router as auth_router
from app.modules.scraper.job.router import router as jobs_router
from app.modules.scraper.place.router import router as places_router
from app.modules.stats.router import router as stats_router

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):  # noqa: ANN201
    setup_logging("api")
    logger.info("{} {} khởi động", settings.app_name, settings.version)
    if settings.jwt_secret_key == "doi-secret-nay-truoc-khi-chay-that" and not settings.debug:
        logger.warning("BCD_JWT_SECRET_KEY vẫn là giá trị mặc định — đổi trước khi chạy thật!")
    _seed_admin()
    yield
    logger.info("{} dừng", settings.app_name)


def _seed_admin() -> None:
    """Tạo tài khoản quản trị đầu tiên nếu bảng users còn rỗng.

    Idempotent và chỉ chạy khi CHƯA có tài khoản nào, để `docker compose up` xong
    là đăng nhập được ngay mà không cần ai gõ thêm lệnh.
    """
    from app.core.security import hash_password
    from app.modules.identity.entity import User
    from app.modules.identity.repository import UserRepository

    db = SessionLocal()
    try:
        repo = UserRepository(db)
        if repo.by_username(settings.admin_username):
            return
        if db.execute(text("SELECT 1 FROM users LIMIT 1")).first():
            return
        repo.add(
            User(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                full_name="Quản trị",
            )
        )
        db.commit()
        logger.info("Đã tạo tài khoản quản trị đầu tiên: {}", settings.admin_username)
    except Exception as exc:  # noqa: BLE001 — DB chưa migrate thì để health báo, đừng chặn khởi động
        logger.warning("Bỏ qua bước tạo tài khoản quản trị: {}", exc)
        db.rollback()
    finally:
        db.close()


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description="Công cụ quét Google Maps lấy danh sách doanh nghiệp (tên, vị trí, SĐT, website).",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url=f"{settings.api_prefix}/openapi.json",
)

app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Request-ID"],
)
register_exception_handlers(app)

health_router = APIRouter(tags=["health"])


@health_router.get("/health", summary="Kiểm tra sống (không cần đăng nhập)")
def health() -> ApiResponse[dict]:
    db_state = "ok"
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_state = f"error: {type(exc).__name__}"
    finally:
        db.close()
    return ApiResponse.ok({"status": "ok", "version": settings.version, "db": db_state})


api = APIRouter(prefix=settings.api_prefix)
api.include_router(health_router)
api.include_router(auth_router)
api.include_router(jobs_router)
api.include_router(places_router)
api.include_router(geo_router)
api.include_router(stats_router)
app.include_router(api)
