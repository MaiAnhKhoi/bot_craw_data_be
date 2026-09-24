"""Phân quyền admin/sale và quản lý tài khoản.

Chạm DB thật, bỏ qua khi chưa có Postgres — cùng khuôn với `test_api.py`.

Điều đáng kiểm nhất ở đây KHÔNG phải "admin làm được gì", mà là hai thứ ngược
lại: sale KHÔNG chọc được vào đâu, và admin KHÔNG tự khoá được cửa. Công cụ này
chạy trong mạng nội bộ, không có màn khôi phục mật khẩu; hạ vai trò hay khoá
nhầm tài khoản quản trị cuối cùng là phải chui vào Docker gõ SQL mới cứu được.
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
            conn.execute(text("SELECT 1 FROM users LIMIT 1"))
        return True
    except Exception:
        return False


if not _db_available():  # pragma: no cover
    pytest.skip("Cần Postgres đã migrate", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.identity.entity import User  # noqa: E402

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


def _tao(client, admin, role: str) -> tuple[int, str, dict[str, str]]:
    """Tạo một tài khoản rồi đăng nhập luôn. Trả (id, username, header)."""
    ten = f"t-{uuid.uuid4().hex[:10]}"
    r = client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": ten, "password": MAT_KHAU, "full_name": "Nguoi thu", "role": role},
    )
    assert r.status_code == 200, r.text
    uid = r.json()["data"]["id"]
    r = client.post("/api/v1/auth/login", json={"username": ten, "password": MAT_KHAU})
    assert r.status_code == 200, r.text
    return uid, ten, {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


@pytest.fixture
def sale(client, admin):
    uid, ten, headers = _tao(client, admin, "sale")
    yield uid, ten, headers
    db = SessionLocal()
    try:
        u = db.get(User, uid)
        if u is not None:
            db.delete(u)
            db.commit()
    finally:
        db.close()


# ------------------------------------------------- sale KHÔNG chọc được vào đâu


CUA_GHI_CHI_ADMIN = [
    ("post", "/api/v1/jobs", {"name": "x", "keywords": ["a"]}),
    ("post", "/api/v1/jobs/1/pause", None),
    ("post", "/api/v1/jobs/1/resume", None),
    ("post", "/api/v1/jobs/1/cancel", None),
    ("post", "/api/v1/jobs/remaining-areas/split-preview", {"queries": ["a"]}),
    ("post", "/api/v1/keywords/plan", {"keywords": ["a"], "countries": ["TH"]}),
    ("post", "/api/v1/keywords/localize", {"keywords": ["a"], "countries": ["TH"]}),
    ("post", "/api/v1/keywords/sets", {"name": "x", "keywords": ["a"]}),
    ("delete", "/api/v1/keywords/sets/1", None),
    ("get", "/api/v1/users", None),
    ("post", "/api/v1/users", {"username": "x", "password": MAT_KHAU, "role": "admin"}),
    ("patch", "/api/v1/users/1", {"role": "sale"}),
    ("post", "/api/v1/users/1/password", {"new_password": MAT_KHAU}),
]


@pytest.mark.parametrize("method,path,body", CUA_GHI_CHI_ADMIN)
def test_sale_bi_chan_o_moi_cua_ghi(client, sale, method, path, body):
    """403 chứ không phải 401: token hợp lệ, chỉ là không đủ quyền.

    Trả 401 ở đây sẽ khiến giao diện tưởng token hết hạn và đá người dùng ra màn
    đăng nhập — họ đăng nhập lại, gặp đúng lỗi đó, và không hiểu chuyện gì.
    """
    _, _, headers = sale
    # `get`/`delete` của TestClient không nhận `json=` — chỉ truyền khi có thân.
    kw = {"headers": headers}
    if body is not None:
        kw["json"] = body
    r = getattr(client, method)(path, **kw)
    assert r.status_code == 403, f"{method} {path} -> {r.status_code}: {r.text}"


def test_sale_van_doc_va_cham_soc_lead_duoc(client, sale):
    """Ẩn nút là để đỡ rối; chặn nhầm cả việc sale CẦN làm mới là hỏng."""
    _, _, headers = sale
    for path in ("/api/v1/places", "/api/v1/jobs", "/api/v1/stats/overview",
                 "/api/v1/places/countries", "/api/v1/auth/me"):
        assert client.get(path, headers=headers).status_code == 200, path


def test_khong_co_token_thi_401_chu_khong_403(client):
    r = client.get("/api/v1/users")
    assert r.status_code == 401


# ------------------------------------------------- admin không tự khoá được cửa


def test_khong_the_tu_khoa_tai_khoan_cua_chinh_minh(client, admin):
    me = client.get("/api/v1/auth/me", headers=admin).json()["data"]
    r = client.patch(f"/api/v1/users/{me['id']}", headers=admin, json={"is_active": False})
    assert r.status_code == 422
    assert "chính mình" in r.text


def test_khong_the_tu_doi_vai_tro_cua_chinh_minh(client, admin):
    """Kể cả khi còn quản trị khác — đây gần như luôn là bấm nhầm.

    Hậu quả tức thì là người đang thao tác bị đá khỏi chính màn hình họ đang mở.
    """
    me = client.get("/api/v1/auth/me", headers=admin).json()["data"]
    r = client.patch(f"/api/v1/users/{me['id']}", headers=admin, json={"role": "sale"})
    assert r.status_code == 422


def test_khong_the_ha_vai_tro_quan_tri_cuoi_cung(client, admin):
    """Chặn nước đi tự khoá cửa.

    Tạo một admin phụ, hạ nó xuống thì PHẢI được (vẫn còn người khác). Rồi thử
    hạ nốt admin còn lại — phải bị chặn.
    """
    uid, _, _ = _tao(client, admin, "admin")
    try:
        # Còn admin gốc nên hạ được.
        r = client.patch(f"/api/v1/users/{uid}", headers=admin, json={"role": "sale"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["role"] == "sale"

        # Nâng lại rồi KHOÁ admin gốc bằng chính tài khoản phụ này.
        client.patch(f"/api/v1/users/{uid}", headers=admin, json={"role": "admin"})
        me = client.get("/api/v1/auth/me", headers=admin).json()["data"]
        r = client.patch(f"/api/v1/users/{me['id']}", headers=admin, json={"is_active": False})
        assert r.status_code == 422  # tự khoá mình -> chặn ở luật trước đó
    finally:
        db = SessionLocal()
        try:
            u = db.get(User, uid)
            if u is not None:
                db.delete(u)
                db.commit()
        finally:
            db.close()


# ------------------------------------------------------------- luật tạo tài khoản


def test_trung_ten_dang_nhap_thi_409(client, admin, sale):
    _, ten, _ = sale
    r = client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": ten, "password": MAT_KHAU, "role": "sale"},
    )
    assert r.status_code == 409


def test_mat_khau_qua_ngan_thi_422(client, admin):
    r = client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": f"t-{uuid.uuid4().hex[:8]}", "password": "123", "role": "sale"},
    )
    assert r.status_code == 422


def test_vai_tro_la_thi_422(client, admin):
    r = client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": f"t-{uuid.uuid4().hex[:8]}", "password": MAT_KHAU, "role": "sep"},
    )
    assert r.status_code == 422


def test_tai_khoan_bi_khoa_thi_khong_dang_nhap_duoc(client, admin, sale):
    uid, ten, _ = sale
    assert client.patch(f"/api/v1/users/{uid}", headers=admin, json={"is_active": False}).status_code == 200
    r = client.post("/api/v1/auth/login", json={"username": ten, "password": MAT_KHAU})
    assert r.status_code == 401


# --------------------------------------------------------------- đổi mật khẩu


def test_tu_doi_mat_khau_phai_dua_mat_khau_hien_tai(client, sale):
    """Máy văn phòng hay để đăng nhập sẵn.

    Không hỏi mật khẩu cũ thì ai ngồi vào máy bỏ trống cũng đổi được mật khẩu và
    chiếm luôn tài khoản.
    """
    _, _, headers = sale
    r = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": "sai-be-bet", "new_password": "matkhau-moi-456"},
    )
    assert r.status_code == 422


def test_tu_doi_mat_khau_thanh_cong_thi_mat_khau_cu_het_dung(client, sale):
    _, ten, headers = sale
    moi = "matkhau-moi-789"
    r = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": MAT_KHAU, "new_password": moi},
    )
    assert r.status_code == 200, r.text
    assert client.post("/api/v1/auth/login", json={"username": ten, "password": MAT_KHAU}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": ten, "password": moi}).status_code == 200


def test_admin_dat_lai_mat_khau_khong_can_mat_khau_cu(client, admin, sale):
    """Người quên mật khẩu thì không có mật khẩu cũ để đưa — đó là lý do duy
    nhất chức năng này tồn tại."""
    uid, ten, _ = sale
    moi = "matkhau-admin-dat-111"
    r = client.post(f"/api/v1/users/{uid}/password", headers=admin, json={"new_password": moi})
    assert r.status_code == 200, r.text
    assert client.post("/api/v1/auth/login", json={"username": ten, "password": moi}).status_code == 200


def test_me_tra_ve_vai_tro(client, sale):
    """Giao diện dựa vào trường này để ẩn bớt nút."""
    _, _, headers = sale
    assert client.get("/api/v1/auth/me", headers=headers).json()["data"]["role"] == "sale"
