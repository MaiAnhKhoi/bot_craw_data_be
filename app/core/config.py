"""Cấu hình toàn hệ thống. Mọi biến môi trường dùng tiền tố `BCD_`."""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BCD_", env_file=".env", extra="ignore")

    # --- ứng dụng ---
    app_name: str = "bot_craw_data"
    version: str = "0.1.0"
    debug: bool = False
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:3000"

    # --- cơ sở dữ liệu ---
    database_url: str = "postgresql+psycopg://botcraw:botcraw@localhost:5432/botcraw"
    db_pool_size: int = 10
    db_max_overflow: int = 10

    # --- bảo mật ---
    jwt_secret_key: str = "doi-secret-nay-truoc-khi-chay-that"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12
    admin_username: str = "admin"
    admin_password: str = "admin12345"   # chỉ dùng để tạo tài khoản đầu tiên

    # --- trình duyệt ---
    browser_engine: str = "playwright"        # playwright | patchright
    headless: bool = True
    browser_profile_dir: str = "var/browser_profile"
    hl: str = "vi"
    gl: str = "vn"
    locale: str = "vi-VN"
    timezone: str = "Asia/Ho_Chi_Minh"
    viewport_width: int = 1366
    viewport_height: int = 900
    page_timeout_ms: int = 30_000

    # --- nhịp cào (1 luồng, IP công ty, không proxy) ---
    pace_min_seconds: float = 4.0
    pace_max_seconds: float = 8.0
    pace_ceiling_seconds: float = 45.0        # trần khi tự giảm tốc vì nghi bị theo dõi
    scroll_pause_min: float = 1.5
    scroll_pause_max: float = 3.0
    block_backoff_seconds: int = 3600         # bị chặn -> nghỉ hẳn 1 giờ
    block_probe_after_pages: int = 200        # chạy êm bấy nhiêu trang thì nới nhịp lại
    night_rest_start: int = 2                 # giờ địa phương bắt đầu nghỉ (2h)
    night_rest_end: int = 6                   # giờ kết thúc nghỉ (6h); đặt bằng nhau = tắt
    worker_poll_seconds: int = 20
    heartbeat_seconds: int = 30

    # --- kiểm tra website (httpx, không dùng trình duyệt) ---
    website_check_timeout: float = 8.0
    website_check_concurrency: int = 8

    # --- xuất file ---
    export_dir: str = "var/exports"
    export_max_rows: int = 100_000

    @field_validator("browser_engine")
    @classmethod
    def _engine(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"playwright", "patchright"}:
            raise ValueError("browser_engine phải là 'playwright' hoặc 'patchright'")
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
