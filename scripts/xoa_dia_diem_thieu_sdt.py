"""Xoá những địa điểm đã quét xong mà không có số điện thoại.

Dọn phần dữ liệu quét TRƯỚC khi có luật `require_phone`. Chạy một lần.

    python scripts/xoa_dia_diem_thieu_sdt.py          # chỉ xem
    python scripts/xoa_dia_diem_thieu_sdt.py --xoa    # xoá thật

CHỈ đụng tới dòng ĐÃ MỞ TRANG CHI TIẾT (`detail_scraped = true`). Dòng chưa mở
thì chưa biết có số hay không — đo thật trên 203 địa điểm, 119 chỉ lộ số sau khi
mở trang. Xoá chúng ở đây là vứt oan đúng những lead đó; cứ để worker mở rồi
luật `require_phone` trong pha chi tiết tự xử.

Mỗi dòng bị xoá để lại một `place_rejects` như lúc quét, nên vẫn soi lại được.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.modules.scraper.place.entity import JobPlace, Place  # noqa: E402
from app.modules.scraper.place.reject import PlaceReject  # noqa: E402


def main() -> None:
    xoa = "--xoa" in sys.argv
    db = SessionLocal()

    ds = list(
        db.execute(
            select(Place)
            .where(Place.phone_e164.is_(None), Place.detail_scraped.is_(True))
            .order_by(Place.id)
        ).scalars().all()
    )
    print(f"{len(ds)} địa điểm đã quét xong mà không có số điện thoại.\n")
    for p in ds[:20]:
        print(f"   {(p.category or '?'):<30} | {(p.name or '')[:46]}")
    if len(ds) > 20:
        print(f"   ... và {len(ds) - 20} dòng nữa")

    con = db.execute(
        select(Place)
        .where(Place.phone_e164.is_(None), Place.detail_scraped.is_(False))
    ).scalars().all()
    print(f"\n{len(con)} dòng chưa mở trang chi tiết -> ĐỂ NGUYÊN, worker sẽ xử.")

    if not xoa:
        print("\n(chỉ xem — thêm --xoa để xoá thật)")
        return

    for p in ds:
        # `job_id` của lần gặp gần nhất, để dòng vết gắn đúng chỗ. Không có job
        # nào (dữ liệu mồ côi) thì bỏ qua phần ghi vết chứ vẫn xoá.
        job_id = db.execute(
            select(JobPlace.job_id)
            .where(JobPlace.place_id == p.id)
            .order_by(JobPlace.job_id.desc())
        ).scalars().first()
        if job_id is not None:
            db.add(
                PlaceReject(
                    job_id=job_id,
                    query="(dọn dữ liệu cũ)",
                    feature_id=(p.feature_id or "")[:64],
                    name=(p.name or "")[:300],
                    category=p.category,
                    maps_url=p.maps_url,
                    source="rule",
                    reason="Không có số điện thoại",
                )
            )
        db.delete(p)
    db.commit()
    print(f"\nĐã xoá {len(ds)} dòng.")


if __name__ == "__main__":
    main()
