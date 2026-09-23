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
