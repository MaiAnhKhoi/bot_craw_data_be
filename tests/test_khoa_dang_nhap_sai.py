"""Khoá tạm khi nhập sai mật khẩu nhiều lần.

Chạm DB thật. Điều đáng kiểm nhất là hai thứ ĐỐI NGHỊCH nhau, và cả hai đều hỏng
âm thầm nếu sai:

  * Chặn được thật — nếu không thì bật tunnel ra Internet là mở cửa cho bot dò
    mật khẩu ở 20 lần/giây (đã đo trên chính hệ thống này).
  * KHÔNG chặn nhầm người dùng thật — khoá cả phòng vì vài người gõ nhầm thì
    ngày hôm đó không ai làm việc được, và người ta sẽ tắt tính năng đi.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db


def _db_available() -> bool:
    try:
        from app.core.database import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM login_ip_blocks LIMIT 1"))
        return True
    except Exception:
        return False


if not _db_available():  # pragma: no cover
    pytest.skip("Cần Postgres đã migrate tới V0013", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.identity.entity import User  # noqa: E402
from app.modules.identity.lockout import LoginIpBlock, con_khoa  # noqa: E402

MAT_KHAU = "matkhau-du-dai-123"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def admin(client) -> dict[str, str]:
    s = get_settings()
    r = client.post(
        "/api/v1/auth/login",
        json={"username": s.admin_username, "password": s.admin_password},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def _xoa_chan_ip() -> None:
    """Xoá bộ đếm theo IP giữa các test.

    BẮT BUỘC: TestClient luôn đi từ cùng một địa chỉ, nên các test cộng dồn vào
    nhau và test thứ hai sẽ đỏ vì bị tầng IP chặn — một lỗi trông y hệt lỗi thật.
    """
    db = SessionLocal()
    try:
        db.query(LoginIpBlock).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def nguoi(client, admin):
    """Một tài khoản dùng một lần, dọn sạch cả dấu vết chặn theo IP sau đó."""
    ten = f"t-{uuid.uuid4().hex[:10]}"
    r = client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": ten, "password": MAT_KHAU, "role": "sale"},
    )
    assert r.status_code == 200, r.text
    uid = r.json()["data"]["id"]
    _xoa_chan_ip()
    yield uid, ten
    db = SessionLocal()
    try:
        u = db.get(User, uid)
        if u is not None:
            db.delete(u)
            db.commit()
    finally:
        db.close()
    _xoa_chan_ip()


def _sai(client, ten: str, lan: int) -> list[int]:
    return [
        client.post(
            "/api/v1/auth/login", json={"username": ten, "password": f"sai-{i}"}
        ).status_code
        for i in range(lan)
    ]


# ------------------------------------------------------------- chặn được thật


def test_sai_qua_nguong_thi_bi_khoa(client, nguoi):
    _, ten = nguoi
    tran = get_settings().login_max_attempts
    assert _sai(client, ten, tran) == [401] * tran

    # Lần kế tiếp là 429, KHÔNG phải 401.
    r = client.post("/api/v1/auth/login", json={"username": ten, "password": "sai-nua"})
    assert r.status_code == 429
    assert "phút" in r.text


def test_khoa_roi_thi_mat_khau_dung_cung_khong_vao_duoc(client, nguoi):
    """Điểm quan trọng nhất: khoá phải chặn TRƯỚC khi so mật khẩu.

    So mật khẩu trước rồi mới xét khoá thì kẻ tấn công vẫn biết được mình đã
    đoán trúng hay chưa — tức là cơ chế khoá không chặn được gì cả.
    """
    _, ten = nguoi
    _sai(client, ten, get_settings().login_max_attempts)
    r = client.post("/api/v1/auth/login", json={"username": ten, "password": MAT_KHAU})
    assert r.status_code == 429


def test_429_noi_ro_con_bao_nhieu_phut(client, nguoi):
    """401 ở đây sẽ khiến người gõ ĐÚNG mật khẩu tưởng mình gõ sai.

    Họ thử lại liên tục, và mỗi lần thử lại kéo dài thêm thời gian khoá.
    """
    _, ten = nguoi
    _sai(client, ten, get_settings().login_max_attempts)
    r = client.post("/api/v1/auth/login", json={"username": ten, "password": "x"})
    assert r.status_code == 429
    assert str(get_settings().login_lock_minutes) in r.text


def test_ten_dang_nhap_khong_ton_tai_van_tinh_vao_tang_ip(client):
    """Không tính thì kẻ tấn công chỉ cần đổi tên đăng nhập mỗi lần là thoát sạch."""
    _xoa_chan_ip()
    try:
        for i in range(3):
            client.post(
                "/api/v1/auth/login",
                json={"username": f"khong-co-ai-{i}", "password": "x"},
            )
        db = SessionLocal()
        try:
            tong = sum(r.failed_attempts for r in db.query(LoginIpBlock).all())
        finally:
            db.close()
        assert tong >= 3
    finally:
        _xoa_chan_ip()


# --------------------------------------------- KHÔNG chặn nhầm người dùng thật


def test_dang_nhap_dung_thi_xoa_sach_bo_dem(client, nguoi):
    """Gõ nhầm vài lần rồi gõ đúng thì phải về số không.

    Không xoá thì mọi lần gõ nhầm rải rác trong ngày cộng dồn lại, và tới chiều
    người dùng bị khoá dù chưa bao giờ sai liên tiếp quá hai lần.
    """
    uid, ten = nguoi
    _sai(client, ten, get_settings().login_max_attempts - 1)
    r = client.post("/api/v1/auth/login", json={"username": ten, "password": MAT_KHAU})
    assert r.status_code == 200

    db = SessionLocal()
    try:
        u = db.get(User, uid)
        assert u.failed_attempts == 0
        assert u.locked_until is None
    finally:
        db.close()


def test_dang_nhap_dung_cung_xoa_bo_dem_theo_ip(client, nguoi):
    """Một người trong phòng đăng nhập được chứng minh đây không phải IP tấn công."""
    _, ten = nguoi
    _sai(client, ten, 3)
    r = client.post("/api/v1/auth/login", json={"username": ten, "password": MAT_KHAU})
    assert r.status_code == 200
    db = SessionLocal()
    try:
        assert all(r.failed_attempts == 0 for r in db.query(LoginIpBlock).all())
    finally:
        db.close()


def test_sai_duoi_nguong_thi_van_vao_duoc_binh_thuong(client, nguoi):
    _, ten = nguoi
    _sai(client, ten, get_settings().login_max_attempts - 1)
    r = client.post("/api/v1/auth/login", json={"username": ten, "password": MAT_KHAU})
    assert r.status_code == 200


# ------------------------------------------------------------- quản trị mở khoá


def test_admin_mo_khoa_thi_dang_nhap_lai_duoc_ngay(client, admin, nguoi):
    uid, ten = nguoi
    _sai(client, ten, get_settings().login_max_attempts)
    r = client.post("/api/v1/auth/login", json={"username": ten, "password": MAT_KHAU})
    assert r.status_code == 429

    r = client.post(f"/api/v1/users/{uid}/unlock", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["locked_until"] is None
    assert r.json()["data"]["failed_attempts"] == 0

    r = client.post("/api/v1/auth/login", json={"username": ten, "password": MAT_KHAU})
    assert r.status_code == 200


def test_sale_khong_mo_khoa_duoc(client, nguoi):
    """Mở khoá là quyền quản trị — sale tự mở được thì cơ chế thành vô nghĩa."""
    uid, ten = nguoi
    tok = client.post("/api/v1/auth/login", json={"username": ten, "password": MAT_KHAU}).json()
    headers = {"Authorization": f"Bearer {tok['data']['access_token']}"}
    assert client.post(f"/api/v1/users/{uid}/unlock", headers=headers).status_code == 403


def test_danh_sach_tai_khoan_lo_trang_thai_khoa(client, admin, nguoi):
    """Giao diện cần nhìn thấy ai đang bị khoá để bấm mở."""
    uid, ten = nguoi
    _sai(client, ten, get_settings().login_max_attempts)
    rows = client.get("/api/v1/users?size=100", headers=admin).json()["data"]["items"]
    dong = next(r for r in rows if r["id"] == uid)
    assert dong["locked_until"] is not None
    assert dong["failed_attempts"] >= get_settings().login_max_attempts


# ----------------------------------------------------------------- hàm thuần


def test_con_khoa_lam_tron_len():
    """Còn 10 giây mà báo 0 phút thì người dùng bấm lại ngay, nhận đúng lỗi đó,
    và tưởng hệ thống hỏng."""
    from datetime import UTC, datetime, timedelta

    assert con_khoa(None) == 0
    assert con_khoa(datetime.now(UTC) - timedelta(minutes=5)) == 0
    assert con_khoa(datetime.now(UTC) + timedelta(seconds=10)) == 1
    assert con_khoa(datetime.now(UTC) + timedelta(minutes=14, seconds=30)) == 15
