"""Test API chạm database thật (Postgres).

Bỏ qua toàn bộ file khi không có DB — nhờ vậy `pytest` vẫn chạy được trên máy chưa
dựng hạ tầng, còn CI thì luôn có service postgres nên vẫn được phủ.

Mỗi test tự dọn dữ liệu của mình để chạy lại nhiều lần vẫn cho cùng kết quả.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db


def _db_available() -> bool:
    try:
        from app.core.database import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM places LIMIT 1"))
        return True
    except Exception:
        return False


if not _db_available():  # pragma: no cover
    pytest.skip(
        "Cần Postgres đã migrate (đặt BCD_DATABASE_URL rồi chạy alembic upgrade head)",
        allow_module_level=True,
    )

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.scraper.engine.models import CardResult, DetailResult  # noqa: E402
from app.modules.scraper.place.entity import JobPlace, Place, PlaceKeyword  # noqa: E402
from app.modules.scraper.place.writer import PlaceWriter, needs_detail  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth(client) -> dict[str, str]:
    s = get_settings()
    r = client.post("/api/v1/auth/login", json={"username": s.admin_username, "password": s.admin_password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


@pytest.fixture
def sample_job(client, auth):
    """Tạo một job + 3 địa điểm gắn với nó, rồi xoá sạch sau khi test xong."""
    tag = uuid.uuid4().hex[:8]
    r = client.post(
        "/api/v1/jobs",
        headers=auth,
        json={"name": f"test-{tag}", "keywords": [f"kw-{tag}"], "locations": ["quận 1"]},
    )
    job_id = r.json()["data"]["id"]

    db = SessionLocal()
    writer = PlaceWriter(db, "VN")
    cards = [
        CardResult(
            name=f"Cong Ty Song {tag}", maps_url="https://maps/1", feature_id=f"0x{tag}:0xa",
            category="Cửa hàng trái cây", address_short="12 Lê Lợi", phone_raw="0901234567",
            rating=4.6, review_count=120, has_hours=True, hours_summary="Đang mở cửa",
        ),
        CardResult(
            name=f"Cong Ty Dong Cua {tag}", maps_url="https://maps/2", feature_id=f"0x{tag}:0xb",
            category="Cửa hàng trái cây", address_short="30 Trần Hưng Đạo",
            business_status="CLOSED_PERMANENTLY", rating=3.2, review_count=4,
        ),
        CardResult(
            name=f"Cong Ty Vang Bong {tag}", maps_url="https://maps/3", feature_id=f"0x{tag}:0xc",
            category="Cửa hàng", address_short="5 Nguyễn Huệ", review_count=0,
        ),
    ]
    ids = []
    for card in cards:
        place, _ = writer.upsert_from_card(card, job_id, f"kw-{tag} quận 1")
        ids.append(place.id)

    alive = db.get(Place, ids[0])
    writer.apply_detail(
        alive,
        DetailResult(
            name=alive.name, address="12 Lê Lợi, Bến Nghé, Quận 1, TP.HCM", phone_raw="0901234567",
            website="https://vidu.vn", category="Cửa hàng trái cây", rating=4.6, review_count=120,
            latest_review_days=45, has_hours=True, feature_id=alive.feature_id,
        ),
    )
    stale = db.get(Place, ids[2])
    writer.apply_detail(
        stale,
        DetailResult(name=stale.name, address="5 Nguyễn Huệ", review_count=2,
                     latest_review_days=1300, feature_id=stale.feature_id),
    )
    for pid in ids:
        p = db.get(Place, pid)
        if p.status == "pending":
            writer.finish_without_detail(p)
    db.close()

    yield {"job_id": job_id, "tag": tag, "place_ids": ids}

    db = SessionLocal()
    db.query(PlaceKeyword).filter(PlaceKeyword.place_id.in_(ids)).delete(synchronize_session=False)
    db.query(JobPlace).filter(JobPlace.job_id == job_id).delete(synchronize_session=False)
    db.query(Place).filter(Place.id.in_(ids)).delete(synchronize_session=False)
    db.execute(text("DELETE FROM job_queries WHERE job_id = :j"), {"j": job_id})
    db.execute(text("DELETE FROM scrape_jobs WHERE id = :j"), {"j": job_id})
    db.commit()
    db.close()



@pytest.fixture
def throwaway_jobs():
    """Thu gom id của job do test tự tạo rồi xoá sạch.

    Không dọn thì mỗi lần chạy test lại để lại job `queued` trong DB, và worker
    thật sẽ nhặt đúng những job rác đó lên chạy.
    """
    created: list[int] = []
    yield created
    if created:
        db = SessionLocal()
        db.execute(text("DELETE FROM job_queries WHERE job_id = ANY(:ids)"), {"ids": created})
        db.execute(text("DELETE FROM job_places WHERE job_id = ANY(:ids)"), {"ids": created})
        db.execute(text("DELETE FROM scrape_jobs WHERE id = ANY(:ids)"), {"ids": created})
        db.commit()
        db.close()

# ---------- health & auth ----------
def test_health_khong_can_dang_nhap(client):
    body = client.get("/api/v1/health").json()
    assert body["success"] is True
    assert body["data"]["db"] == "ok"
    assert body["request_id"] != "-"


def test_dang_nhap_sai_mat_khau_tra_401(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "sai-mat-khau"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"
    # Không được tiết lộ tài khoản có tồn tại hay không.
    assert "mật khẩu" in r.json()["error"]["message"].lower()


def test_thieu_token_tra_401(client):
    assert client.get("/api/v1/places").status_code == 401


def test_me_tra_ve_tai_khoan(client, auth):
    data = client.get("/api/v1/auth/me", headers=auth).json()["data"]
    assert data["username"] == get_settings().admin_username
    assert data["is_active"] is True


# ---------- jobs ----------
def test_tao_job_nhan_to_hop_va_khu_trung_lap(client, auth, throwaway_jobs):
    r = client.post(
        "/api/v1/jobs",
        headers=auth,
        json={
            "name": "to hop",
            "keywords": ["a", " a ", "b", "# bỏ qua"],
            "locations": ["q1", "q3"],
        },
    )
    assert r.status_code == 200, r.text
    job = r.json()["data"]
    throwaway_jobs.append(job["id"])
    assert job["total_queries"] == 4        # 2 từ khoá (đã khử trùng) x 2 địa điểm
    queries = [q["query"] for q in client.get(f"/api/v1/jobs/{job['id']}", headers=auth).json()["data"]["queries"]]
    # Gom theo ĐỊA ĐIỂM trước, từ khoá sau: mọi từ khoá của cùng một nơi chạy liền
    # nhau. Nhờ vậy hl/gl và bối cảnh địa lý giữ nguyên qua các truy vấn kề nhau
    # (trông tự nhiên hơn là nhảy qua lại giữa các nước), và job bị huỷ giữa chừng
    # thì vẫn phủ TRỌN VẸN vài địa bàn thay vì phủ dở dang khắp nơi.
    assert queries == ["a q1", "b q1", "a q3", "b q3"]


def test_tao_job_khong_co_tu_khoa_bi_tu_choi(client, auth):
    r = client.post("/api/v1/jobs", headers=auth, json={"name": "rỗng", "keywords": []})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_vong_doi_job(client, auth, sample_job):
    job_id = sample_job["job_id"]
    assert client.post(f"/api/v1/jobs/{job_id}/pause", headers=auth).json()["data"]["status"] == "paused"

    again = client.post(f"/api/v1/jobs/{job_id}/pause", headers=auth)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "JOB_INVALID_STATE"

    assert client.post(f"/api/v1/jobs/{job_id}/resume", headers=auth).json()["data"]["status"] == "queued"
    assert client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth).json()["data"]["status"] == "cancelled"


def test_job_khong_ton_tai(client, auth):
    assert client.get("/api/v1/jobs/99999999", headers=auth).status_code == 404


# ---------- places ----------
def test_chấm_diem_song_chet_tu_du_lieu_that(client, auth, sample_job):
    items = client.get(
        "/api/v1/places", headers=auth, params={"job_id": sample_job["job_id"], "size": 50}
    ).json()["data"]["items"]
    by_name = {i["name"]: i for i in items}
    tag = sample_job["tag"]

    song = by_name[f"Cong Ty Song {tag}"]
    assert song["liveness_label"] == "ACTIVE" and song["liveness_score"] == 100
    assert song["liveness_reasons"] == []
    assert song["phone"] and song["phone_e164"] == "+84901234567" and song["phone_valid"] is True
    assert song["address"].endswith("TP.HCM")          # địa chỉ đầy đủ đè bản rút gọn
    assert song["website"] == "https://vidu.vn"

    dong = by_name[f"Cong Ty Dong Cua {tag}"]
    # Google khẳng định đóng cửa vĩnh viễn -> kết luận luôn, không xét tín hiệu khác.
    assert dong["liveness_label"] == "DEAD" and dong["liveness_score"] == 0
    assert dong["liveness_reasons"] == ["CLOSED_PERMANENTLY"]

    vang = by_name[f"Cong Ty Vang Bong {tag}"]
    assert vang["liveness_label"] == "DEAD"
    assert "REVIEWS_STALE_3Y" in vang["liveness_reasons"]
    assert "NO_PHONE" in vang["liveness_reasons"]


def test_bo_loc_places(client, auth, sample_job):
    job_id = sample_job["job_id"]
    tag = sample_job["tag"]

    def names(**params) -> set[str]:
        params.setdefault("job_id", job_id)
        params.setdefault("size", 50)
        data = client.get("/api/v1/places", headers=auth, params=params).json()["data"]
        return {i["name"] for i in data["items"]}

    assert names(liveness=["ACTIVE"]) == {f"Cong Ty Song {tag}"}
    assert names(has_phone=True) == {f"Cong Ty Song {tag}"}
    assert names(has_website=False) == {f"Cong Ty Dong Cua {tag}", f"Cong Ty Vang Bong {tag}"}
    assert names(business_status="CLOSED_PERMANENTLY") == {f"Cong Ty Dong Cua {tag}"}
    assert names(min_rating=4.0) == {f"Cong Ty Song {tag}"}
    assert names(keyword=f"kw-{tag} quận 1") == {
        f"Cong Ty Song {tag}", f"Cong Ty Dong Cua {tag}", f"Cong Ty Vang Bong {tag}"
    }
    # tìm không dấu phải khớp chuỗi có dấu
    assert f"Cong Ty Dong Cua {tag}" in names(q="tran hung dao")
    assert f"Cong Ty Song {tag}" in names(q="Lê Lợi")


def test_sap_xep_va_phan_trang(client, auth, sample_job):
    data = client.get(
        "/api/v1/places", headers=auth,
        params={"job_id": sample_job["job_id"], "sort": "-liveness", "size": 2, "page": 1},
    ).json()["data"]
    assert data["size"] == 2 and data["total"] == 3 and data["pages"] == 2
    assert len(data["items"]) == 2
    scores = [i["liveness_score"] for i in data["items"]]
    assert scores == sorted(scores, reverse=True)

    page2 = client.get(
        "/api/v1/places", headers=auth,
        params={"job_id": sample_job["job_id"], "sort": "-liveness", "size": 2, "page": 2},
    ).json()["data"]
    assert len(page2["items"]) == 1


def test_reverify_dua_ve_hang_doi(client, auth, sample_job):
    pid = sample_job["place_ids"][0]
    before = client.get(f"/api/v1/places/{pid}", headers=auth).json()["data"]
    assert before["detail_scraped"] is True
    client.post(f"/api/v1/places/{pid}/reverify", headers=auth)
    db = SessionLocal()
    try:
        assert db.get(Place, pid).status == "pending"
    finally:
        db.close()


def test_place_khong_ton_tai(client, auth):
    r = client.get("/api/v1/places/99999999", headers=auth)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


# ---------- xuất file ----------
@pytest.mark.parametrize(
    ("fmt", "sniff"),
    [("xlsx", b"PK"), ("csv", "Tên công ty"), ("json", '"liveness_label"')],
)
def test_xuat_file(client, auth, sample_job, fmt, sniff):
    r = client.get(
        "/api/v1/places/export", headers=auth, params={"job_id": sample_job["job_id"], "format": fmt}
    )
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    if isinstance(sniff, bytes):
        assert r.content.startswith(sniff)       # xlsx là file zip
    else:
        assert sniff in r.content.decode("utf-8-sig")


def test_xuat_file_sai_dinh_dang(client, auth):
    r = client.get("/api/v1/places/export", headers=auth, params={"format": "pdf"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_csv_co_bom_cho_excel(client, auth, sample_job):
    r = client.get(
        "/api/v1/places/export", headers=auth, params={"job_id": sample_job["job_id"], "format": "csv"}
    )
    assert r.content.startswith(b"\xef\xbb\xbf")


# ---------- thống kê ----------
def test_overview(client, auth, sample_job):
    data = client.get("/api/v1/stats/overview", headers=auth).json()["data"]
    assert data["total"] >= 3
    assert set(data["by_liveness"]) == {"ACTIVE", "SUSPECT", "DEAD"}
    assert set(data["completeness"]) == {"phone", "website", "address"}
    assert len(data["last_14_days"]) == 14
    assert data["today_new"] >= 3


def test_worker_status_bao_chet_khi_khong_co_nhip_tim(client, auth):
    data = client.get("/api/v1/stats/worker", headers=auth).json()["data"]
    assert set(data) >= {"alive", "current_phase", "pace_seconds", "blocked_today", "night_rest"}
    assert isinstance(data["alive"], bool)


# ---------- quyết định mở trang chi tiết ----------
def test_needs_detail_bo_qua_noi_da_dong_cua_vinh_vien(sample_job):
    db = SessionLocal()
    try:
        closed = next(
            p for p in (db.get(Place, i) for i in sample_job["place_ids"])
            if p.business_status == "CLOSED_PERMANENTLY"
        )
        # Đã có kết luận từ Google thì mở trang chi tiết là lãng phí.
        assert needs_detail(closed, "always", True, 90) is False
        assert needs_detail(closed, "missing_only", True, 90) is False
    finally:
        db.close()


def test_needs_detail_ton_trong_ttl(sample_job):
    db = SessionLocal()
    try:
        fresh = db.get(Place, sample_job["place_ids"][0])
        assert fresh.detail_scraped is True
        assert needs_detail(fresh, "missing_only", True, 90) is False   # còn trong hạn
        # Hết hạn (hoặc tắt TTL) thì phải quét lại dù không thiếu trường nào —
        # đây chính là cơ chế phát hiện công ty đã ngừng hoạt động theo thời gian.
        assert needs_detail(fresh, "missing_only", True, 0) is True
        assert needs_detail(fresh, "always", True, 0) is True
        assert needs_detail(fresh, "never", True, 0) is False
    finally:
        db.close()


def test_env_khong_lo_secret_qua_health(client):
    body = client.get("/api/v1/health").text
    assert os.environ.get("BCD_JWT_SECRET_KEY", "khong-ton-tai") not in body
