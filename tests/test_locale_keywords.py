"""Ngôn ngữ/quốc gia tìm kiếm theo từng truy vấn, và từ khoá bản địa.

Toàn bộ chạy offline: không DB, không mạng, không gọi AI.
"""
from __future__ import annotations

import pytest

from app.core import ai
from app.modules.geo import service as geo
from app.modules.keyword.service import countries_of_locations, normalize, source_hash
from app.modules.scraper.job.service import expand_queries


# ---------- nhận diện quốc gia từ chuỗi địa điểm ----------
@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Phường Bến Thành, Thành phố Hồ Chí Minh, Việt Nam", "VN"),
        ("Thành phố Hà Nội, Việt Nam", "VN"),
        ("Việt Nam", "VN"),
        ("Bangkok, Thailand", "TH"),
        ("Thailand", "TH"),
        ("Tokyo, Japan", "JP"),
        ("Badakhshān, Afghanistan", "AF"),
    ],
)
def test_nhan_dien_quoc_gia_tu_duoi_chuoi(location, expected):
    assert geo.resolve_country(location)["code"] == expected


@pytest.mark.parametrize("location", ["Quận 1", "Chợ đầu mối Thủ Đức", "", None, "khong-co-that"])
def test_khong_nhan_ra_thi_tra_none(location):
    assert geo.resolve_country(location) is None


def test_ten_tieng_anh_cung_khop():
    assert geo.resolve_country("Hanoi, Viet Nam")["code"] == "VN"


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ("Seoul, South Korea", "KR"),
        ("Taipei, Taiwan", "TW"),
        # Tên ISO có dấu phẩy BÊN TRONG — chỉ lấy đoạn cuối thì ra "Republic of"
        # và không khớp gì cả, rồi âm thầm rơi về vi/vn tức là quét nhầm sang
        # Việt Nam. 15 nước dính lỗi này, gồm cả Hàn Quốc và Đài Loan.
        ("Seoul, Korea, Republic of", "KR"),
        ("Taipei, Taiwan, Province of China", "TW"),
        ("Tehran, Iran, Islamic Republic of", "IR"),
        ("Dar es Salaam, Tanzania, United Republic of", "TZ"),
    ],
)
def test_ten_quoc_gia_nhieu_doan_van_nhan_ra(location, expected):
    assert geo.resolve_country(location)["code"] == expected
    assert geo.locale_for_location(location)[1] == expected.lower()


def test_khong_quoc_gia_nao_con_dau_phay_trong_chuoi_truy_van():
    """Dấu phẩy trong `query` làm vỡ việc nhận diện từ đuôi chuỗi địa điểm.

    `scripts/build_geo_data.py` cũng chặn điều này lúc sinh dữ liệu; test ở đây là
    lưới thứ hai cho trường hợp ai đó sửa tay `geo.json`.
    """
    xau = [c["code"] for c in geo.load().countries if "," in c["query"]]
    assert xau == [], f"các nước còn dấu phẩy: {xau}"


def test_ten_hien_thi_cua_thi_truong_chinh_doc_duoc():
    """Bản dịch tự động cho ra "Cộng hoà Nam Hàn", "Phi-li-pi-nợ" — sửa tay."""
    by_code = {c["code"]: c for c in geo.load().countries}
    assert by_code["KR"]["name"] == "Hàn Quốc"
    assert by_code["KR"]["query"] == "South Korea"
    assert by_code["TW"]["name"] == "Đài Loan"
    assert by_code["JP"]["name"] == "Nhật Bản"


# ---------- chọn hl/gl ----------
def test_viet_nam_dung_tieng_viet():
    assert geo.locale_for("VN") == ("vi", "vn")


def test_nuoc_ngoai_dung_tieng_anh_khong_dung_tieng_ban_dia():
    """CỐ Ý chỉ vi/en: bộ bóc tách chỉ đọc được hai ngôn ngữ đó.

    Đặt hl=th thì thẻ kết quả ra tiếng Thái, và mọi tín hiệu chấm sống/chết
    (trạng thái mở cửa, "đã đóng cửa vĩnh viễn", tuổi đánh giá) im lặng thành rỗng.
    """
    assert geo.locale_for("TH") == ("en", "th")
    assert geo.locale_for("JP") == ("en", "jp")
    assert geo.locale_for("US") == ("en", "us")


def test_khong_biet_quoc_gia_thi_ve_mac_dinh():
    assert geo.locale_for(None) == ("vi", "vn")
    assert geo.locale_for("") == ("vi", "vn")
    assert geo.locale_for_location("Quận 1") == ("vi", "vn")
    assert geo.locale_for_location("Quận 1", default=("en", "us")) == ("en", "us")


def test_locale_theo_tung_dia_diem():
    assert geo.locale_for_location("Bangkok, Thailand") == ("en", "th")
    assert geo.locale_for_location("Thành phố Hồ Chí Minh, Việt Nam") == ("vi", "vn")


# ---------- mở rộng truy vấn ----------
def test_moi_truy_van_mang_locale_rieng():
    qs = expand_queries(["vựa trái cây"], ["Thành phố Hồ Chí Minh, Việt Nam", "Bangkok, Thailand"])
    assert [(q.hl, q.gl) for q in qs] == [("vi", "vn"), ("en", "th")]


def test_tu_khoa_rieng_theo_quoc_gia():
    qs = expand_queries(
        ["vựa trái cây"],
        ["Thành phố Hồ Chí Minh, Việt Nam", "Bangkok, Thailand"],
        keyword_map={"TH": ["fruit wholesaler", "ผู้ค้าส่งผลไม้"]},
    )
    texts = [q.query for q in qs]
    assert "vựa trái cây Thành phố Hồ Chí Minh, Việt Nam" in texts
    assert "fruit wholesaler Bangkok, Thailand" in texts
    assert "ผู้ค้าส่งผลไม้ Bangkok, Thailand" in texts
    # Từ khoá tiếng Việt KHÔNG được dùng cho địa điểm Thái khi đã có bản đồ riêng
    assert "vựa trái cây Bangkok, Thailand" not in texts


def test_ma_quoc_gia_trong_ban_do_khong_phan_biet_hoa_thuong():
    qs = expand_queries(["x"], ["Bangkok, Thailand"], keyword_map={"th": ["fruit wholesaler"]})
    assert [q.query for q in qs] == ["fruit wholesaler Bangkok, Thailand"]


def test_quoc_gia_khong_co_trong_ban_do_thi_dung_tu_khoa_goc():
    qs = expand_queries(["vựa trái cây"], ["Tokyo, Japan"], keyword_map={"TH": ["fruit wholesaler"]})
    assert [q.query for q in qs] == ["vựa trái cây Tokyo, Japan"]
    assert qs[0].gl == "jp"


def test_khong_co_dia_diem_thi_chi_la_tu_khoa():
    qs = expand_queries(["a", "b"], [])
    assert [q.query for q in qs] == ["a", "b"]
    assert all((q.hl, q.gl) == ("vi", "vn") for q in qs)


def test_khu_trung_lap_va_bo_dong_chu_thich():
    qs = expand_queries(["a", " a ", "# bỏ qua", "b"], ["q1", "# bỏ qua", "q1"])
    assert [q.query for q in qs] == ["a q1", "b q1"]


def test_locale_mac_dinh_cua_job_duoc_ton_trong():
    qs = expand_queries(["x"], ["Quận 1"], default_locale=("en", "us"))
    assert (qs[0].hl, qs[0].gl) == ("en", "us")


# ---------- khoá bộ nhớ đệm từ khoá ----------
def test_chuan_hoa_tu_khoa():
    assert normalize(["  vựa   trái cây ", "Vựa trái cây", "", "x"]) == ["vựa trái cây", "x"]


def test_khoa_dem_khong_phu_thuoc_thu_tu_va_hoa_thuong():
    assert source_hash(["a", "b"]) == source_hash(["B", "a"])
    assert source_hash(["a"]) != source_hash(["a", "b"])


def test_gom_quoc_gia_tu_danh_sach_dia_diem():
    countries = countries_of_locations(
        [
            "Thành phố Hồ Chí Minh, Việt Nam",
            "Thành phố Hà Nội, Việt Nam",   # trùng nước, chỉ tính một lần
            "Bangkok, Thailand",
            "Quận 1",                        # không nhận ra -> bỏ qua
        ]
    )
    assert [c["code"] for c in countries] == ["VN", "TH"]


# ---------- AI tắt thì hệ thống vẫn chạy ----------
def test_ai_tat_thi_bao_ro_chu_khong_im_lang(monkeypatch):
    monkeypatch.setattr(ai, "is_enabled", lambda: False)
    with pytest.raises(ai.AiUnavailable):
        ai.localize_keywords(["vựa trái cây"], [{"code": "TH", "name_en": "Thailand"}])


def test_khong_co_khoa_thi_coi_nhu_tat():
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.ai_api_key:
        assert ai.is_enabled() is False
