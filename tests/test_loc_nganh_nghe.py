"""Bộ lọc ngành nghề: chấm điểm, chặn lúc ghi, và lưu vết cái đã chặn.

Phần chấm điểm chạy THUẦN (không DB, không mạng). Phần chặn lúc ghi cần DB vì cái
đáng kiểm chính là "bảng `places` có thêm dòng hay không" — kiểm bằng giả lập thì
chỉ chứng minh được hàm đã được gọi, không chứng minh được dữ liệu sạch.

Dữ liệu trong `NGANH_IN` / `KQ_THAT` lấy nguyên từ job "Công ty trái cây" quét quần
đảo Andaman (174 địa điểm) — chính lượt quét đã sinh ra tiệm bánh kem mà người
dùng báo lỗi. Giữ nguyên số liệu thật để bộ test này còn là một bằng chứng chứ
không chỉ là một phép kiểm.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.modules.scraper.engine.models import CardResult
from app.modules.scraper.job.entity import ScrapeJob
from app.modules.scraper.place.entity import Place
from app.modules.scraper.place.reject import PlaceReject
from app.modules.scraper.place.relevance import (
    LIEN_QUAN,
    NGHI_RAC,
    RANH_GIOI,
    cham,
    gop,
    nhan_chung_chung,
    tach_tu,
)
from app.modules.scraper.place.writer import PlaceWriter

# Danh mục ngành nghề AI sinh cho Ấn Độ với bộ từ khoá ["Công ty trái cây"].
NGANH_IN = [
    "फल थोक व्यापारी", "फल और सब्जी की दुकान", "फल विक्रेता", "थोक विक्रेता",
    "किराना स्टोर", "बाजार", "सुपरमार्केट", "खेत",
    "Fruit wholesaler", "Fruit and vegetable store", "Produce supplier",
    "Grocery store", "Wholesale distributor", "Market", "Supermarket", "Farm",
]

# (ngành nghề, tên, có phải thứ mình đi tìm không) — lấy từ dữ liệu thật.
KQ_THAT = [
    ("Fruit and vegetable store", "AR FRUIT SHOP", True),
    ("Fruit wholesaler", "Md Israil Fruits Cold Stores", True),
    ("Produce market", "Andaman Greengrocers", True),
    ("Grocery store", "VMR SUPERMART", True),
    ("किराना दुकान", "Aboo Pan & Super Market", True),
    ("फल सब्जी की दुकान", "M.M Vegetable Shop", True),
    ("Market", "Vegetable and Fish Market", True),
    ("Supermarket", "Nepal Super Marketing", True),
    ("Wholesaler", "Sree Murugan Traders", True),
    # Đây là những thứ người dùng báo lỗi.
    ("Bakery", "Tillai's Bakery & Sweets", False),
    ("Cake shop", "Tasty Treats Andaman", False),
    ("Confectionery store", "Calcutta Sweet Stall", False),
    ("Ice cream shop", "Cone Cafe", False),
    ("Travel agency", "Maven Andaman", False),
    ("Tour operator", "Andaman Bliss", False),
    ("Pet store", "MK PET SHOP"[:40], False),
    ("Pharmacy", "YUVARAJ PHARMACY", False),
    ("Men's clothing store", "Liv young", False),
    ("Historical landmark", "Kala Pani jail", False),
    ("Transportation service", "Makruzz Private Limited", False),
    ("Book store", "Sagarika Emporium", False),
    ("Tile store", "Group engineers corporation", False),
]


# --------------------------------------------------------------- chấm điểm


def test_khong_co_danh_muc_thi_khong_cham_chu_khong_coi_la_rac():
    """Job không khai danh mục -> None, và None KHÔNG BAO GIỜ bị loại.

    Đây là cái van an toàn của cả tính năng. Trả `NGHI_RAC` ở đây nghĩa là mọi job
    cũ, mọi job không gọi AI, mọi job Việt Nam đều bị vứt sạch kết quả.
    """
    assert cham("Bakery", "Tillai's Bakery", []) is None
    assert cham("Bakery", "Tillai's Bakery", None) is None


def test_chua_co_nganh_nghe_thi_khong_doan_theo_moi_cai_ten():
    """Google chưa gắn nhãn -> None.

    Đoán bừa từ tên sẽ vứt nhầm doanh nghiệp đặt tên trung tính; "Công ty TNHH
    Minh Phát" không có chữ nào dính trái cây nhưng vẫn có thể là nhà nhập khẩu.
    """
    assert cham(None, "Công ty TNHH Minh Phát", NGANH_IN) is None
    assert cham("   ", "Công ty TNHH Minh Phát", NGANH_IN) is None


@pytest.mark.parametrize("category,name,dung_nganh", KQ_THAT)
def test_cham_dung_tren_du_lieu_that(category: str, name: str, dung_nganh: bool):
    mong_doi = LIEN_QUAN if dung_nganh else NGHI_RAC
    assert cham(category, name, NGANH_IN) == mong_doi


def test_nhan_ban_ngu_va_nhan_tieng_anh_cung_bat_duoc():
    """Google đổi nhãn theo ngôn ngữ giao diện, nên danh mục phải phủ cả hai vế.

    Cùng một cửa hàng lúc ra "फल विक्रेता" lúc ra "Fruit and vegetable store".
    Thiếu một vế là lọc nhầm hàng loạt — đã đo: danh mục chỉ có tiếng Anh thì 9
    cửa hàng tạp hoá Ấn Độ bị vứt oan.
    """
    assert cham("किराना दुकान", "Aboo Pan", NGANH_IN) == LIEN_QUAN
    assert cham("Grocery store", "VMR SUPERMART", NGANH_IN) == LIEN_QUAN


def test_nhan_vo_nghia_thi_khong_quyet_ma_day_sang_ranh_gioi():
    """"Company", "General store", "Cửa hàng" không nói gì về mặt hàng.

    Loại thẳng là vứt oan `Battambang Agro Industry Co., Ltd.` (Google gắn nhãn
    "Company") — doanh nghiệp nông sản thật. Giữ thẳng là mời mọi cửa hàng tạp
    hoá vào bảng. Cả hai đều sai, nên luật cứng phải nhận là mình không quyết được.
    """
    assert nhan_chung_chung("Company") is True
    assert nhan_chung_chung("General store") is True
    assert nhan_chung_chung("Cửa hàng") is True
    assert nhan_chung_chung("Corporate office") is True
    # Nhãn có nghĩa thì KHÔNG đẩy sang AI — tốn token mà chẳng thêm thông tin gì.
    assert nhan_chung_chung("Bakery") is False
    assert nhan_chung_chung("Fruit wholesaler") is False

    assert cham("Company", "Battambang Agro Industry Co., Ltd.", NGANH_IN) == RANH_GIOI
    assert cham("Cửa hàng", "Rahim Fruits Stall", ["Fruit wholesaler"]) == RANH_GIOI


def test_chi_khop_qua_TEN_thi_cung_la_ranh_gioi():
    """Nhãn ngành RÕ RÀNG là thứ khác, chỉ mỗi cái tên dính chữ.

    Đủ để nghi, không đủ để kết luận. Cùng một luật này vừa cứu
    "Rahim Fruits Stall" vừa để lọt "Dress store", "Book store" — đo trên dữ
    liệu thật. Nên nó phải đi hỏi thêm chứ không được tự quyết.
    """
    assert cham("Dress store", "Fruit Fashion", NGANH_IN) == RANH_GIOI
    assert cham("Book store", "Farm House Books", NGANH_IN) == RANH_GIOI


def test_nhan_ro_rang_khac_nganh_thi_luat_cung_tu_quyet(): 
    """Không đẩy sang AI những ca luật đã chắc chắn — đó là phần lớn số thẻ.

    "Bakery", "Travel agency", "Pharmacy": nhãn rõ nghĩa, rõ ràng khác ngành,
    tên cũng không dính. Gửi cả đám này cho AI là đốt token cho một câu trả lời
    đã biết trước.
    """
    assert cham("Bakery", "Tillai's Bakery & Sweets", NGANH_IN) == NGHI_RAC
    assert cham("Travel agency", "Maven Andaman", NGANH_IN) == NGHI_RAC
    assert cham("Pharmacy", "YUVARAJ PHARMACY", NGANH_IN) == NGHI_RAC


def test_nhan_trong_chuoi_cung_ung_thi_khong_loai_thang():
    """Lưới an toàn cho chỗ yếu nhất: danh mục do AI sinh, người dùng không sửa được.

    AI quên một nhãn là cả một loại doanh nghiệp biến mất không dấu vết. Đo trên
    174 địa điểm Andaman với một danh mục 12 nhãn thật: luật cứng loại 68 dòng,
    trong đó 6 dòng đáng tiếc (3 "Supermarket", "Food products supplier",
    "Food Processing Company", "Packaging company" — đóng gói trái cây). Lưới này
    đẩy cả 6 sang cho AI xem lại, chỉ tốn thêm 7 lượt hỏi.
    """
    khong_co_trong_danh_muc = ["Fruit wholesaler", "फल विक्रेता"]
    for nhan in ("Supermarket", "Food Processing Company", "Packaging company",
                 "Food products supplier", "Vegetable wholesale market"):
        assert cham(nhan, "X", khong_co_trong_danh_muc) == RANH_GIOI, nhan


def test_luoi_chuoi_cung_ung_khong_duoc_keo_theo_ca_pho():
    """Cố ý KHÔNG bắt từ thương mại chung chung.

    Thử với "store/shop/company/supplier/enterprise" thì nó kéo thêm 30 dòng, gần
    hết là "Pet store", "Tile store", "Men's clothing store", "Computer store" —
    gấp bốn lần chi phí để cứu đúng chừng ấy dòng.
    """
    for nhan in ("Pet store", "Tile store", "Men's clothing store", "Computer store",
                 "Solar Energy Company", "Building materials supplier"):
        assert cham(nhan, "X", NGANH_IN) == NGHI_RAC, nhan


def test_tu_chung_chung_khong_duoc_tinh_la_bang_chung():
    """"supplier" bị loại khỏi vốn từ có nghĩa.

    Danh mục có "Produce supplier" thì chỉ riêng chữ "supplier" đủ để kéo theo
    "Building materials supplier", "Printing equipment supplier", "Electrical
    equipment supplier" — đã đo, cả ba đều lọt lưới trước khi sửa.
    """
    assert "supplier" not in tach_tu("Produce supplier")
    assert cham("Building materials supplier", "Shree Om Traders", NGANH_IN) == NGHI_RAC
    # Mà "Produce supplier" vẫn còn "produce" để khớp, nên không mất gì.
    assert cham("Produce supplier", "X", NGANH_IN) == LIEN_QUAN


def test_diem_chi_duoc_nang_khong_duoc_ha():
    """Một địa điểm nằm trong nhiều job với nhiều danh mục khác nhau.

    Job sau hạ điểm xuống là một lead đã xác nhận tự biến mất khỏi bảng mà không
    ai biết — đúng kiểu hỏng âm thầm mà dự án này sợ nhất.
    """
    assert gop(LIEN_QUAN, NGHI_RAC) == LIEN_QUAN
    assert gop(LIEN_QUAN, None) == LIEN_QUAN
    assert gop(NGHI_RAC, LIEN_QUAN) == LIEN_QUAN
    assert gop(None, NGHI_RAC) == NGHI_RAC
    # Lượt này không chấm được thì giữ nguyên cái đã biết.
    assert gop(NGHI_RAC, None) == NGHI_RAC
    # Ba mức chứ không phải hai: "chưa chắc" mạnh hơn "nghi lạc đề", yếu hơn
    # "đúng ngành". Một lượt AI hỏng không được xoá kết luận AI đã phán lần trước.
    assert gop(LIEN_QUAN, RANH_GIOI) == LIEN_QUAN
    assert gop(RANH_GIOI, NGHI_RAC) == RANH_GIOI
    assert gop(RANH_GIOI, LIEN_QUAN) == LIEN_QUAN


# --------------------------------------------------- chặn ngay lúc ghi (DB)


@pytest.fixture
def job_trong():
    """Một job rỗng, xoá sạch sau khi test xong (cascade kéo theo place_rejects)."""
    db = SessionLocal()
    job = ScrapeJob(name=f"test-loc-{uuid.uuid4().hex[:8]}", status="queued", phase="idle", params={})
    db.add(job)
    db.commit()
    db.refresh(job)
    yield db, job
    # Xoá địa điểm TRƯỚC job: `job_places` trỏ vào cả hai, và ở chiều này thì
    # cascade của job không dọn hộ được bảng `places`.
    db.execute(Place.__table__.delete().where(Place.feature_id.like(f"test-{job.id}-%")))
    db.delete(db.get(ScrapeJob, job.id))
    db.commit()
    db.close()


def _the(job_id: int, hau_to: str, name: str, category: str) -> CardResult:
    return CardResult(
        name=name,
        maps_url=f"https://maps/{hau_to}",
        feature_id=f"test-{job_id}-{hau_to}",
        category=category,
    )


def test_the_ngoai_danh_muc_khong_vao_bang_dia_diem(job_trong):
    """Cái người dùng thật sự yêu cầu: loại NGAY, không để nó vào làm bẩn dữ liệu."""
    db, job = job_trong
    writer = PlaceWriter(db, "IN")

    place, is_new = writer.upsert_from_card(
        _the(job.id, "banh", "Tillai's Bakery & Sweets", "Bakery"),
        job.id, "फल व्यापारी Andaman", country_code="IN", cho_phep=NGANH_IN,
    )
    assert place is None
    assert is_new is False
    assert db.execute(
        select(func.count(Place.id)).where(Place.feature_id == f"test-{job.id}-banh")
    ).scalar_one() == 0


def test_the_dung_danh_muc_van_vao_binh_thuong_va_duoc_danh_dau(job_trong):
    db, job = job_trong
    writer = PlaceWriter(db, "IN")
    place, is_new = writer.upsert_from_card(
        _the(job.id, "traicay", "AR FRUIT SHOP", "Fruit and vegetable store"),
        job.id, "फल व्यापारी Andaman", country_code="IN", cho_phep=NGANH_IN,
    )
    assert place is not None and is_new is True
    assert place.relevance == LIEN_QUAN


def test_loai_bo_khong_bao_gio_im_lang(job_trong):
    """Mỗi thẻ bị loại phải để lại vết soi được, và phải đếm được trên job.

    Không có hai thứ này thì một danh mục thiếu nhãn sẽ âm thầm vứt cả một loại
    doanh nghiệp thật, và không có cách nào phát hiện ngoài việc thấy thiếu lead.
    """
    db, job = job_trong
    writer = PlaceWriter(db, "IN")
    for i, (cat, ten) in enumerate([("Bakery", "Tillai's"), ("Travel agency", "Maven")]):
        writer.upsert_from_card(
            _the(job.id, f"rac{i}", ten, cat),
            job.id, "फल व्यापारी Andaman", country_code="IN", cho_phep=NGANH_IN,
        )

    rows = db.execute(select(PlaceReject).where(PlaceReject.job_id == job.id)).scalars().all()
    assert len(rows) == 2
    assert {r.category for r in rows} == {"Bakery", "Travel agency"}
    assert all(r.query == "फल व्यापारी Andaman" for r in rows)
    assert db.get(ScrapeJob, job.id).rejected_count == 2


def test_khong_khai_danh_muc_thi_ghi_het_nhu_cu(job_trong):
    """Job cũ và job không gọi AI phải chạy y như trước khi có tính năng này."""
    db, job = job_trong
    writer = PlaceWriter(db, "IN")
    place, is_new = writer.upsert_from_card(
        _the(job.id, "khonglo", "Tillai's Bakery & Sweets", "Bakery"),
        job.id, "kw", country_code="IN", cho_phep=[],
    )
    assert place is not None and is_new is True
    assert place.relevance is None
    assert db.get(ScrapeJob, job.id).rejected_count == 0


def test_dia_diem_da_co_trong_bang_thi_khong_bi_xoa(job_trong):
    """Job A đã nhận nó là đúng ngành thì job B với danh mục hẹp hơn không được vứt.

    Xoá ở đây là để danh mục của job B quyết định thay cho job A — và người dùng
    của job A mất lead mà không hề biết.
    """
    db, job = job_trong
    writer = PlaceWriter(db, "IN")
    # Nhãn rõ ràng ngoài ngành, để phép kiểm này nói về LUẬT KHÔNG XOÁ chứ không
    # vô tình dính sang lưới "chuỗi cung ứng thực phẩm".
    the = _the(job.id, "chung", "Andaman Naturals", "Tile store")

    dau, _ = writer.upsert_from_card(the, job.id, "kw rong", country_code="IN", cho_phep=[])
    assert dau is not None
    place_id = dau.id

    sau, is_new = writer.upsert_from_card(
        the, job.id, "kw hep", country_code="IN", cho_phep=NGANH_IN
    )
    assert sau is not None and is_new is False
    assert db.get(Place, place_id) is not None
    # Vẫn được ghi nhận là nghi lạc đề để lọc thấy, nhưng dòng dữ liệu còn nguyên.
    assert sau.relevance == NGHI_RAC
    assert db.get(ScrapeJob, job.id).rejected_count == 0


def test_trang_chi_tiet_doi_nhan_thi_phai_cham_lai(job_trong):
    """Lỗi thật đã gặp: hai lead bị giấu khỏi bảng vì một cái nhãn không còn tồn tại.

    Thẻ kết quả và trang chi tiết thường ghi nhãn khác nhau — hay gặp nhất là thẻ
    tiếng bản ngữ còn trang chi tiết tiếng Anh. `apply_detail` ghi đè
    `place.category`, nên nếu không chấm lại thì kết luận cũ nằm lại trên nhãn cũ.

    Đo thật: "AKR FRESH VEG & FROZEN FOODS PVT LTD" và "Rangat Bazar" mang nhãn
    "Fruit and vegetable wholesaler" — nằm thẳng trong danh mục — mà `relevance`
    vẫn là `weak`. Nhìn vào bảng thì thấy mâu thuẫn không giải thích nổi.
    """
    from app.modules.scraper.engine.models import DetailResult

    db, job = job_trong
    writer = PlaceWriter(db, "IN")
    # Thẻ ghi một nhãn KHÔNG có trong danh mục -> ranh giới (có "market" trong tên).
    place, _ = writer.upsert_from_card(
        _the(job.id, "doinhan", "Rangat Bazar Market", "Cold storage facility"),
        job.id, "kw", country_code="IN", cho_phep=NGANH_IN, diem=NGHI_RAC, nguon="ai",
    )
    assert place is None  # bị loại theo phán quyết truyền vào

    place, _ = writer.upsert_from_card(
        _the(job.id, "doinhan2", "Rangat Bazar", "Cold storage facility"),
        job.id, "kw", country_code="IN", cho_phep=NGANH_IN, diem=NGHI_RAC, nguon="rule",
    )
    assert place is None

    # Dòng đã nằm sẵn trong bảng với điểm `weak`, rồi trang chi tiết đổi nhãn.
    cu, _ = writer.upsert_from_card(
        _the(job.id, "cu", "Rangat Bazar", "Cold storage facility"),
        job.id, "kw", country_code="IN", cho_phep=[],
    )
    cu.relevance, cu.relevance_source = NGHI_RAC, "rule"
    db.flush()

    writer.apply_detail(
        cu,
        DetailResult(name="Rangat Bazar", category="Fruit and vegetable wholesaler",
                     feature_id=cu.feature_id),
        cho_phep=NGANH_IN,
    )
    assert cu.category == "Fruit and vegetable wholesaler"
    assert cu.relevance == LIEN_QUAN
    assert cu.relevance_source == "rule"


def test_cham_lai_o_pha_chi_tiet_chi_duoc_nang(job_trong):
    """Trang chi tiết biết rõ hơn về NHÃN, nhưng thẻ mới là thứ đã qua đủ bước.

    Cho phép hạ ở đây thì một phán quyết "giữ" của AI (đã nhìn cả tên) bị một cái
    nhãn chung chung xoá mất, mà không ai thấy.
    """
    from app.modules.scraper.engine.models import DetailResult

    db, job = job_trong
    writer = PlaceWriter(db, "IN")
    p, _ = writer.upsert_from_card(
        _the(job.id, "aigiu", "Battambang Agro Industry", "Company"),
        job.id, "kw", country_code="IN", cho_phep=NGANH_IN,
        diem=LIEN_QUAN, nguon="ai", ly_do="Tên gợi công ty nông sản",
    )
    assert p is not None and p.relevance == LIEN_QUAN

    writer.apply_detail(
        p,
        DetailResult(name=p.name, category="Corporate office", feature_id=p.feature_id),
        cho_phep=NGANH_IN,
    )
    assert p.relevance == LIEN_QUAN
    assert p.relevance_reason == "Tên gợi công ty nông sản"


def test_dia_diem_bi_job_huy_chot_so_duoc_mo_lai_hang_doi(job_trong):
    """Huỷ job giữa chừng không được làm địa điểm kẹt vĩnh viễn ở địa chỉ rỗng.

    `close_pending_of_job` chốt sổ địa điểm dang dở bằng dữ liệu của thẻ kết quả
    khi người dùng bấm Huỷ. Job sau nhặt đúng địa điểm ấy, thấy `done`, và pha
    chi tiết bỏ qua — nó nằm lại mãi mãi không có địa chỉ đầy đủ, không có
    website, KHÔNG BÁO LỖI GÌ. Đo thật: huỷ job #2 rồi chạy job #7 trên cùng địa
    bàn, 6 dòng `done` / `detail_scraped=false` / `attempts=0`, trông y hệt đã xong.
    """
    from app.modules.scraper.place.entity import (
        PLACE_DONE,
        PLACE_PENDING,
    )

    db, job = job_trong
    writer = PlaceWriter(db, "IN")
    the = _the(job.id, "kethuc", "AR FRUIT SHOP", "Fruit and vegetable store")

    p, _ = writer.upsert_from_card(the, job.id, "kw", country_code="IN", cho_phep=NGANH_IN)
    # Người dùng bấm Huỷ -> chốt sổ bằng dữ liệu thẻ, chưa từng mở trang chi tiết.
    writer.finish_without_detail(p)
    assert p.status == PLACE_DONE and p.detail_scraped is False

    # Job sau gặp lại nó.
    lai, is_new = writer.upsert_from_card(the, job.id, "kw2", country_code="IN", cho_phep=NGANH_IN)
    assert is_new is False
    assert lai.status == PLACE_PENDING


def test_dia_diem_dong_cua_vinh_vien_thi_khong_mo_lai_hang_doi(job_trong):
    """Nếu không chừa ca này ra thì mỗi lượt quét lại đẩy chúng vào một vòng quẩn.

    `needs_detail` cố ý không mở trang chi tiết của địa điểm Google đã khẳng định
    đóng cửa vĩnh viễn, nên mở lại hàng đợi chỉ tạo ra pending -> done vô ích.
    """
    from app.modules.scraper.engine.models import CardResult
    from app.modules.scraper.place.entity import PLACE_DONE

    db, job = job_trong
    writer = PlaceWriter(db, "IN")
    the = CardResult(
        name="AR FRUIT SHOP", maps_url="https://maps/dong", feature_id=f"test-{job.id}-dong",
        category="Fruit and vegetable store", business_status="CLOSED_PERMANENTLY",
    )
    p, _ = writer.upsert_from_card(the, job.id, "kw", country_code="IN", cho_phep=NGANH_IN)
    writer.finish_without_detail(p)
    lai, _ = writer.upsert_from_card(the, job.id, "kw2", country_code="IN", cho_phep=NGANH_IN)
    assert lai.status == PLACE_DONE


# ------------------------------------------- bắt buộc có số điện thoại


def test_khong_co_sdt_thi_bi_xoa_va_de_lai_vet(job_trong):
    """Số điện thoại là thứ DUY NHẤT dùng được ở đây — không có thì không gọi được.

    Xoá chứ không ẩn, vì người dùng yêu cầu rõ "không cần ghi vào dữ liệu".
    Nhưng không được mất dấu: một dòng `place_rejects` giữ tên, ngành nghề,
    truy vấn và link Maps, đủ để soi lại hoặc quét tay.
    """
    from sqlalchemy import func
    from sqlalchemy import select as sel

    db, job = job_trong
    writer = PlaceWriter(db, "IN")
    p, _ = writer.upsert_from_card(
        _the(job.id, "khongsdt", "AR FRUIT SHOP", "Fruit and vegetable store"),
        job.id, "kw", country_code="IN", cho_phep=NGANH_IN,
    )
    assert p is not None and p.phone_e164 is None
    pid = p.id

    assert writer.bo_vi_thieu_sdt(p, job.id, "kw") is True
    assert db.get(Place, pid) is None

    vet = db.execute(sel(PlaceReject).where(PlaceReject.job_id == job.id)).scalars().all()
    assert [v.reason for v in vet] == ["Không có số điện thoại"]
    assert vet[0].name == "AR FRUIT SHOP"
    assert db.get(ScrapeJob, job.id).rejected_count == 1
    # Bộ đếm của job đếm lại từ DB nên không hỏng theo.
    assert db.execute(sel(func.count()).select_from(Place).where(Place.id == pid)).scalar_one() == 0


def test_co_sdt_thi_khong_dung_toi(job_trong):
    """Địa điểm đã có số KHÔNG BAO GIỜ rơi vào nhánh xoá.

    Nhờ vậy một job sau không thể xoá mất lead mà job trước đã lấy được số.
    """
    from app.modules.scraper.engine.models import CardResult

    db, job = job_trong
    writer = PlaceWriter(db, "IN")
    the = CardResult(
        name="AR FRUIT SHOP", maps_url="https://maps/sdt", feature_id=f"test-{job.id}-sdt",
        category="Fruit and vegetable store", phone_raw="+91 99332 93338",
    )
    p, _ = writer.upsert_from_card(the, job.id, "kw", country_code="IN", cho_phep=NGANH_IN)
    assert p.phone_e164 == "+919933293338"
    assert writer.bo_vi_thieu_sdt(p, job.id, "kw") is False
    assert db.get(Place, p.id) is not None
    assert db.get(ScrapeJob, job.id).rejected_count == 0
