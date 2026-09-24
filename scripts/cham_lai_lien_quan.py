"""Chấm lại ngành nghề cho những địa điểm quét TRƯỚC khi có bộ lọc.

VÌ SAO CẦN — bộ lọc chỉ chặn lúc GHI. Địa điểm đã nằm sẵn trong bảng thì
`upsert_from_card` cố ý không xoá (xoá là để job này quyết định thay job khác),
nên toàn bộ dữ liệu quét trước ngày có tính năng vẫn còn nguyên tiệm bánh kem,
đại lý du lịch, hiệu sách — và `relevance` của chúng là NULL, nghĩa là "chưa
chấm được", nên đường đọc vẫn hiện ra.

Script này chấm bù. CHẠY MỘT LẦN, không phải việc định kỳ.

    python scripts/cham_lai_lien_quan.py            # chỉ xem, không ghi
    python scripts/cham_lai_lien_quan.py --ghi      # ghi thật
    python scripts/cham_lai_lien_quan.py --ghi --ai # ghi, và hỏi AI phần ranh giới
    python scripts/cham_lai_lien_quan.py --ghi --ai --sua-sai   # xem thêm ở dưới

`--sua-sai` chấm lại CẢ những dòng đang là "đúng ngành" và CHO PHÉP HẠ ĐIỂM.

Bình thường điểm chỉ được nâng, không được hạ (xem `relevance.gop`) — luật đó
bảo vệ lead khỏi bị một job có danh mục hẹp hơn xoá mất. Nhưng nó cũng khiến một
kết luận SAI đóng băng vĩnh viễn: "Tasty Treats Andaman" (Cake shop) và
"M.M Pizza Coffee Fruit Juice" (quán cà phê) được giữ lại bởi bản luật cũ, và
không lượt quét nào sửa được nữa.

Chỉ dùng bằng tay, sau khi đã sửa luật hoặc sửa prompt. Worker KHÔNG BAO GIỜ
chạy ở chế độ này.

KHÔNG XOÁ DÒNG NÀO. Nó chỉ đặt `relevance`; những dòng thành `weak` sẽ bị đường
đọc giấu đi, và `?relevance=weak` vẫn soi lại được. Xoá thật là việc của người
dùng sau khi đã nhìn.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import or_, select  # noqa: E402

from app.core import ai  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.modules.keyword.service import HOME_COUNTRY, loc_cum_hong  # noqa: E402
from app.modules.scraper.job.entity import ScrapeJob  # noqa: E402
from app.modules.scraper.place.entity import JobPlace, Place  # noqa: E402
from app.modules.scraper.place.relevance import (  # noqa: E402
    LIEN_QUAN,
    NGHI_RAC,
    RANH_GIOI,
    cham,
    gop,
)
from app.modules.scraper.place.writer import danh_muc_cho_place  # noqa: E402


def danh_muc_cua(db, place: Place) -> list[str]:
    """Danh mục để chấm một địa điểm cũ — dùng chung hàm của worker.

    Cố ý KHÔNG viết lại luật tra ở đây: script chấm bù mà dùng một luật khác với
    lúc quét thì hai lần chấm cùng một địa điểm ra hai kết quả, và không ai biết
    cái nào đúng. Chỉ bổ sung một nhánh worker không cần: Việt Nam lấy từ khoá
    làm danh mục (sân nhà không đi qua AI nên không có dòng nào trong bảng dịch).
    """
    ds = loc_cum_hong(danh_muc_cho_place(db, place))
    if ds:
        return ds
    if (place.country_code or "").upper() == HOME_COUNTRY:
        params = db.execute(
            select(ScrapeJob.params)
            .join(JobPlace, JobPlace.job_id == ScrapeJob.id)
            .where(JobPlace.place_id == place.id)
            .order_by(ScrapeJob.id.desc())
        ).scalars().all()
        for pr in params:
            kw = (pr or {}).get("keywords") or []
            if kw:
                return list(kw)
    return []


def main() -> None:
    ghi = "--ghi" in sys.argv
    dung_ai = "--ai" in sys.argv
    sua_sai = "--sua-sai" in sys.argv
    db = SessionLocal()

    # Lấy CẢ nhóm `weak`, không chỉ nhóm chưa chấm. Những dòng `weak` cũ được
    # chấm trên nhãn của THẺ KẾT QUẢ, rồi trang chi tiết ghi đè một nhãn khác mà
    # không chấm lại — đó là vì sao "Fruit and vegetable wholesaler" nằm thẳng
    # trong danh mục mà vẫn bị giấu khỏi bảng. `gop` chỉ nâng chứ không hạ nên
    # quét lại nhóm này không thể làm mất thêm dòng nào.
    stmt = select(Place).order_by(Place.id)
    if not sua_sai:
        stmt = stmt.where(or_(Place.relevance.is_(None), Place.relevance == NGHI_RAC))
    chua_cham = list(db.execute(stmt).scalars().all())
    pham_vi = "TẤT CẢ, cho phép hạ điểm" if sua_sai else "chưa chấm, hoặc đang là lạc đề"
    print(f"{len(chua_cham)} địa điểm cần chấm lại ({pham_vi}).\n")

    dem = Counter()
    ranh_gioi: list[Place] = []
    for p in chua_cham:
        moi = cham(p.category, p.name, danh_muc_cua(db, p))
        diem = moi if sua_sai else gop(p.relevance, moi)
        dem[diem] += 1
        if diem == RANH_GIOI:
            ranh_gioi.append(p)
        elif diem is not None and ghi and diem != p.relevance:
            p.relevance, p.relevance_source, p.relevance_reason = diem, "rule", None

    print(f"  luật giữ  : {dem[LIEN_QUAN]}")
    print(f"  luật loại : {dem[NGHI_RAC]}")
    print(f"  ranh giới : {dem[RANH_GIOI]}")
    print(f"  không đủ căn cứ để chấm (để nguyên): {dem[None]}\n")

    if ranh_gioi and dung_ai:
        uv = [
            ai.UngVien(stt=i, name=p.name, category=p.category, address=p.address or p.address_short)
            for i, p in enumerate(ranh_gioi)
        ]
        # Lấy từ khoá gốc của job đầu tiên làm mô tả mặt hàng — mọi job trong DB
        # này đều cùng một ngành, nên một mô tả là đủ và tiết kiệm được lượt gọi.
        job = db.execute(select(ScrapeJob).order_by(ScrapeJob.id)).scalars().first()
        mat_hang = list((job.params or {}).get("keywords") or ["Công ty trái cây"]) if job else []
        try:
            kq = ai.judge_places(mat_hang, uv)
        except ai.AiUnavailable as exc:
            print(f"  AI không phán được ({exc}) — để nguyên {len(ranh_gioi)} dòng ranh giới.")
            kq = {}
        for i, p in enumerate(ranh_gioi):
            px = kq.get(i)
            if px is None:
                continue
            print(f"  {'GIỮ ' if px.giu else 'LOẠI'} | {(p.category or '?'):<26} | {p.name[:40]:<42} | {px.ly_do}")
            if ghi:
                p.relevance = LIEN_QUAN if px.giu else NGHI_RAC
                p.relevance_source, p.relevance_reason = "ai", px.ly_do
    elif ranh_gioi and ghi:
        for p in ranh_gioi:
            p.relevance, p.relevance_source = RANH_GIOI, "rule"

    if ghi:
        db.commit()
        print("\nĐã ghi.")
    else:
        print("\n(chỉ xem — thêm --ghi để ghi thật)")


if __name__ == "__main__":
    main()
