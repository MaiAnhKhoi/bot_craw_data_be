"""
Hạ tầng test dùng chung cho bộ test của engine cào Google Maps.

Nguyên tắc: bộ test này chỉ chạm vào PHẦN THUẦN của engine (parsers, normalize,
liveness, pacing, build_detail). Không DB, không mạng, không Google — nên chạy
được ở mọi máy và mọi lúc, kể cả khi Google đổi giao diện.

Marker:
    browser  cần Chromium của Playwright, nhưng CHẠY OFFLINE trên file HTML mẫu
             trong tests/fixtures/ (dùng page.set_content, không goto ra internet).
    live     bắn thẳng vào Google Maps thật — chỉ chạy tay khi cần kiểm selector.

pyproject.toml đã khai báo sẵn hai marker này; phần `pytest_configure` dưới đây
chỉ là lưới an toàn cho trường hợp chạy pytest với rootdir khác (ví dụ chạy thẳng
`pytest tests/test_parsers.py` từ thư mục khác) để không dính cảnh báo
PytestUnknownMarkWarning.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Cho phép `import app...` kể cả khi pytest được gọi từ thư mục khác thư mục gốc
# dự án (bản editable-install trong .venv đang trỏ tới src/ của kiến trúc cũ).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _chuyen_sang_db_test() -> None:
    """Trỏ toàn bộ bộ test sang MỘT DATABASE RIÊNG, tạo sẵn nếu chưa có.

    ★ Phải chạy TRƯỚC khi bất kỳ module nào import `app.core.database` — engine ở
    đó được dựng ngay lúc import, từ `get_settings()`. conftest.py được pytest nạp
    đầu tiên nên đây là chỗ duy nhất còn kịp.

    VÌ SAO BẮT BUỘC, chứ không phải cho sạch sẽ: `tests/test_api.py` gọi thật
    `POST /jobs`, và job sinh ra nằm ở trạng thái `queued`. Worker THẬT đang chạy
    cùng lúc thì `claim_next()` vồ đúng những job đó rồi đi quét Google bằng từ
    khoá rác. Đo thực tế trên database của người dùng: 35 truy vấn dạng
    "kw-22693068 quận 1" đã chạy thật, 66 liên kết từ khoá rác lẫn vào bảng
    địa điểm, và 37 job nữa đang xếp hàng chờ bắn tiếp.

    Fixture `throwaway_jobs` có dọn, nhưng dọn ở lúc teardown là QUÁ MUỘN: worker
    hỏi hàng đợi vài giây một lần, còn bộ test chạy vài chục giây — worker luôn
    thắng cuộc đua đó. Không có cách nào vá bằng dọn dẹp; phải tách hẳn dữ liệu.

    Không dựng được (chưa có Postgres) thì im lặng bỏ qua: `test_api.py` tự
    `skip` khi không kết nối được, đúng như thiết kế sẵn có.
    """
    import os

    try:
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url

        from app.core.config import get_settings

        goc = make_url(get_settings().database_url)
        if goc.database and goc.database.endswith("_test"):
            return                                    # đã trỏ sẵn, không làm gì
        ten_test = f"{goc.database}_test"

        # CREATE DATABASE không chạy được trong transaction, và Postgres không có
        # IF NOT EXISTS cho nó -> tra pg_database rồi tạo ở chế độ AUTOCOMMIT.
        quan_tri = create_engine(goc.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with quan_tri.connect() as conn:
            co = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :ten"), {"ten": ten_test}
            ).first()
            if not co:
                conn.execute(text(f'CREATE DATABASE "{ten_test}"'))
        quan_tri.dispose()

        # `str(url)` của SQLAlchemy CHE mật khẩu thành "***" — dùng thẳng nó thì
        # alembic chết bằng "password authentication failed" mà không ai hiểu vì
        # sao (đã dính thật).
        os.environ["BCD_DATABASE_URL"] = goc.set(database=ten_test).render_as_string(
            hide_password=False
        )
        get_settings.cache_clear()

        # Nâng schema cho DB test. Idempotent nên chạy mỗi lượt pytest đều được.
        import subprocess

        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=PROJECT_ROOT, capture_output=True, check=False,
        )
    except Exception:  # noqa: BLE001 — không dựng được thì để test_api tự skip
        return


_chuyen_sang_db_test()

MARKERS = (
    "browser: cần Playwright Chromium (vẫn offline, chạy trên HTML mẫu)",
    "live: bắn vào Google Maps thật; chỉ chạy khi GMAPS_LIVE=1",
)

def pytest_configure(config: pytest.Config) -> None:
    for marker in MARKERS:
        config.addinivalue_line("markers", marker)


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Thư mục chứa HTML mẫu của khung chi tiết Google Maps."""
    return FIXTURES_DIR


@pytest.fixture(scope="module")
def chromium_page():
    """Một trang Chromium headless dùng chung cho cả module test browser.

    Mở trình duyệt tốn ~1 giây nên dùng chung; mỗi test vẫn độc lập vì luôn
    `set_content` lại toàn bộ tài liệu trước khi đọc.
    """
    sync_playwright = pytest.importorskip(
        "playwright.sync_api", reason="cần playwright để chạy EXTRACT_JS thật"
    ).sync_playwright

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001 — chưa `playwright install chromium`
            pytest.skip(f"không khởi động được Chromium: {exc}")
        page = browser.new_page()
        try:
            yield page
        finally:
            browser.close()
