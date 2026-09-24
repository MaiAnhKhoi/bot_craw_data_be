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
    db_connect_timeout: int = 5   # giây; tối thiểu psycopg chấp nhận là 2

    # --- bảo mật ---
    jwt_secret_key: str = "doi-secret-nay-truoc-khi-chay-that"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 12
    admin_username: str = "admin"
    admin_password: str = "admin12345"   # chỉ dùng để tạo tài khoản đầu tiên

    # --- khoá tạm khi nhập sai mật khẩu nhiều lần ---
    #
    # Đo thật trên hệ thống này: bcrypt đã làm chậm sẵn còn ~20 lần thử mỗi giây.
    # Mật khẩu ngẫu nhiên 8 ký tự thường cần ~325 năm nên vốn đã an toàn — nhưng
    # mật khẩu kiểu `sale2026`, `Ago@2026` nằm trong vài nghìn cái phổ biến đầu
    # tiên, tức là rụng trong DƯỚI 5 PHÚT. Nhân viên sẽ đặt đúng kiểu đó.
    #
    # Khoá 15 phút kéo 20 lần/giây xuống còn ~20 lần mỗi 15 phút — chậm hơn
    # 900 lần, đủ để mật khẩu yếu cũng thành không dò nổi.
    login_max_attempts: int = 5
    # Ngưỡng theo IP cao hơn hẳn ngưỡng theo tài khoản: một văn phòng dùng chung
    # một IP ra Internet, mà 10 sale gõ nhầm rải rác trong ngày là chuyện thường.
    # Đặt thấp là cả phòng bị khoá vì vài người đãng trí.
    login_ip_max_attempts: int = 20
    # Ngắn có chủ đích. Khoá dài hơn chẳng chặn thêm được gì đáng kể (900 lần đã
    # quá đủ) nhưng biến mọi lần gõ nhầm thành nửa buổi không làm việc được. Nó
    # cũng là van an toàn: quản trị tự khoá mình vẫn vào lại được sau 15 phút mà
    # không cần nhờ ai.
    login_lock_minutes: int = 15

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

    # --- AI sinh từ khoá bản địa (tuỳ chọn, tắt mặc định) ---
    # Chỉ dùng lúc TẠO JOB để gợi ý từ khoá theo quốc gia; worker cào không bao giờ
    # gọi AI. Không cấu hình khoá thì giao diện ẩn nút dịch và mọi thứ chạy như cũ.
    ai_enabled: bool = True
    ai_api_key: str = ""
    # Tác vụ này chỉ là tra cụm từ tìm kiếm bản địa — model nhẹ là đủ và rẻ.
    ai_model: str = "claude-haiku-4-5"
    ai_max_keywords: int = 4
    ai_max_countries: int = 40      # trần số quốc gia cho một lần gọi

    # --- tầng AI chấm lại những ca ranh giới lúc quét ---
    #
    # Đây là NGOẠI LỆ DUY NHẤT của luật "worker không bao giờ gọi AI". Luật đó
    # có để một lần AI hỏng không làm chết job đang chạy — ngoại lệ này giữ đúng
    # tinh thần ấy: AI hỏng thì thẻ ranh giới được GIỮ LẠI kèm dấu nghi ngờ, job
    # chạy tiếp như không có gì. Không có đường nào để AI làm hỏng một lượt quét.
    ai_judge_enabled: bool = True
    # Trần số thẻ đưa cho AI trong MỘT truy vấn. Google trả tối đa ~120 thẻ mỗi
    # truy vấn và phần ranh giới đo được chỉ khoảng 10–20%, nên 60 là rất rộng —
    # nó ở đây làm cầu chì cho trường hợp danh mục ngành nghề bị khai sai khiến
    # gần như mọi thẻ rơi vào ranh giới. Vượt trần thì phần dư giữ lại kèm dấu
    # nghi ngờ, không phải vứt đi.
    ai_judge_max_per_query: int = 60

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
