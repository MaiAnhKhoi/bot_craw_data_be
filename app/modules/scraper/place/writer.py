"""Lớp ghi dữ liệu của worker: thẻ kết quả / trang chi tiết -> bảng places.

Tách riêng khỏi `service.py` (phục vụ API đọc) vì đây là đường ghi, có luật khác:
idempotent theo `feature_id`, và mỗi địa điểm commit ngay để worker chết giữa
chừng vẫn không mất việc đã làm.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.geo import borders
from app.modules.geo import service as geo
from app.modules.scraper.engine.models import CardResult, DetailResult
from app.modules.scraper.engine.normalize import normalize_phone
from app.modules.scraper.job.entity import ScrapeJob
from app.modules.scraper.place.entity import (
    BUSINESS_CLOSED_PERM,
    PLACE_DONE,
    PLACE_FAILED,
    PLACE_PENDING,
    JobPlace,
    Place,
    PlaceKeyword,
)
from app.modules.scraper.place.reject import PlaceReject
from app.modules.scraper.place.relevance import NGHI_RAC
from app.modules.scraper.place.relevance import cham as cham_lien_quan
from app.modules.scraper.place.relevance import gop as gop_lien_quan
from app.modules.scraper.place.service import build_search_text, recompute_liveness


def _now() -> datetime:
    return datetime.now(UTC)


def country_of_address(address: str | None) -> str | None:
    """Mã quốc gia đọc từ ĐUÔI địa chỉ, ví dụ "..., Bangkok 10300, Thailand" -> TH.

    Đây là nguồn đáng tin nhất vì nó là thứ Google tự ghi ra cho chính địa điểm đó,
    trong khi `gl` chỉ nói ta đã TÌM ở nước nào — hai thứ lệch nhau ở vùng giáp biên.
    """
    found = geo.resolve_country(address)
    return found["code"] if found else None


# Độ tin cậy của từng nguồn xác định quốc gia. Số lớn hơn thì thắng.
#
#   address  Google tự ghi tên nước ở đuôi địa chỉ CỦA CHÍNH địa điểm đó.
#            Chắc chắn nhất, nhưng thẻ kết quả thường chỉ cho địa chỉ rút gọn
#            nên phần lớn thời gian không có.
#   coords   Toạ độ nằm trong biên giới nước nào. Google cho toạ độ ở MỌI thẻ
#            (đo thật: 100% số dòng), không phụ thuộc ngôn ngữ hay cách viết
#            địa chỉ. Sai số cỡ 1-2 km ở sát biên giới.
#   gl       Chỉ nói ta đã TÌM ở nước nào, KHÔNG nói địa điểm NẰM ở nước nào.
#            Yếu nhất, chỉ dùng khi không còn gì khác.
DO_TIN_NGUON = {"address": 3, "coords": 2, "gl": 1}


def resolve_place_country(
    address: str | None,
    existing: str | None,
    existing_source: str | None,
    query_gl: str | None,
    lat: float | None = None,
    lng: float | None = None,
) -> tuple[str | None, str | None]:
    """Chốt quốc gia của một địa điểm. Trả `(mã ISO alpha-2, nguồn)`.

    Chọn nguồn MẠNH NHẤT đang có chứ không xét theo thứ tự cố định — nhờ vậy một
    lần quét sau có địa chỉ đầy đủ sẽ nâng cấp được kết luận cũ vốn chỉ suy từ
    `gl`, còn `gl` thì không bao giờ ghi đè được thứ mạnh hơn.

    Vì sao phải trả kèm NGUỒN: đây là một phỏng đoán, và một phỏng đoán vô hình
    là thứ nguy hiểm nhất trong dự án này. Đoán sai quốc gia kéo theo đọc sai số
    điện thoại nội địa, mà số sai vẫn "hợp lệ" nên không ai phát hiện. Lưu nguồn
    lại thì người dùng lọc ra được đúng những dòng đáng ngờ để soi.
    """
    ung_vien: list[tuple[str, str]] = []
    tu_dia_chi = country_of_address(address)
    if tu_dia_chi:
        ung_vien.append((tu_dia_chi, "address"))
    tu_toa_do = borders.country_at(lat, lng)
    if tu_toa_do:
        ung_vien.append((tu_toa_do, "coords"))
    if existing:
        ung_vien.append((existing.upper(), existing_source or "gl"))
    gl = (query_gl or "").strip().upper()
    if gl:
        ung_vien.append((gl, "gl"))
    if not ung_vien:
        return None, None
    # `max` giữ phần tử ĐẦU khi bằng điểm, nên thứ tự thêm ở trên cũng là thứ tự
    # ưu tiên trong cùng một mức tin cậy.
    return max(ung_vien, key=lambda x: DO_TIN_NGUON.get(x[1], 0))


def region_of(place: Place, fallback: str = "VN") -> str:
    """Vùng dùng để phân tích số điện thoại của địa điểm này.

    KHÔNG được dùng một vùng cố định cho cả job. `phonenumbers` diễn giải số NỘI
    ĐỊA theo vùng được truyền vào:

        "02 281 9715"  (Bangkok)  vùng VN -> +8422819715   sai, số không tồn tại
        "081 939 8727" (Bangkok)  vùng VN -> +84819398727  sai NHƯNG HỢP LỆ

    Ca thứ hai mới nguy hiểm: nó qua được `is_valid_number`, hiện màu bình thường
    trên giao diện và nằm im trong file xuất cho sale gọi.

    Phạm vi ảnh hưởng: chỉ số lấy từ THẺ KẾT QUẢ, vì thẻ ghi số theo dạng nội địa.
    Trang chi tiết trả về dạng quốc tế ("+6622819715") — có sẵn mã nước nên vùng
    không còn ý nghĩa. Nghĩa là lỗi này nằm im cho tới khi chạy chế độ
    `detail_mode=never`, hoặc `missing_only` mà tắt kiểm tra website (khi đó thẻ
    đã đủ dữ liệu nên worker không mở trang chi tiết nữa).
    """
    return (place.country_code or fallback or "VN").upper()


def needs_detail(place: Place, detail_mode: str, enrich_website: bool, ttl_days: int) -> bool:
    """Có phải mở trang chi tiết cho địa điểm này không.

    Đây là chỗ tiết kiệm thời gian lớn nhất của cả hệ thống, nên quyết định phải rõ:

    KHÔNG mở khi:
    1. Google đã khẳng định đóng cửa vĩnh viễn -> kết luận đã đủ, mở thêm vô ích.
    2. Chế độ `never` -> chỉ dùng những gì thẻ kết quả cho sẵn.
    3. Dữ liệu chi tiết còn trong hạn TTL -> vừa quét xong, chưa có gì đổi.

    CÓ mở khi đã hết hạn tươi và: chế độ `always`, hoặc còn thiếu trường nghiệp vụ,
    hoặc bản ghi đã quá hạn và cần xác minh lại xem công ty còn hoạt động không.
    """
    if place.business_status == BUSINESS_CLOSED_PERM:
        return False
    if detail_mode == "never":
        return False

    still_fresh = (
        place.detail_scraped
        and place.scraped_at is not None
        and ttl_days > 0
        and place.scraped_at > _now() - timedelta(days=ttl_days)
    )
    if still_fresh:
        return False
    if detail_mode == "always":
        return True

    # missing_only — thiếu SĐT hoặc thiếu ĐỊA CHỈ ĐẦY ĐỦ thì phải mở trang chi tiết.
    #
    # `place.address` giờ chỉ nhận địa chỉ từ trang chi tiết, nên điều kiện này
    # cũng chính là "chưa có địa chỉ dùng được". Thẻ kết quả rút gọn tới mức vô
    # dụng — đo thật: 39% số dòng KHÔNG có mẩu nào, phần còn lại dài trung bình
    # 20 ký tự ("Phan Huy Ích"). Địa chỉ là một trong bốn trường nghiệp vụ bắt
    # buộc, nên mẩu đó không thể tính là đã có.
    #
    # Toạ độ đã cứu được phần QUỐC GIA (nhờ đó số điện thoại đọc đúng vùng),
    # nhưng không cứu được phần địa chỉ. Hai việc khác nhau.
    if not place.phone_e164 or not place.address:
        return True
    if not place.detail_scraped:
        # Website chỉ có trên trang chi tiết, thẻ kết quả không bao giờ có —
        # nên chỉ mở khi người dùng thật sự cần website.
        return enrich_website
    # Đã từng quét chi tiết nhưng đã quá hạn: mở lại để biết công ty còn sống không.
    # Thiếu bước này thì dữ liệu cũ sẽ nằm im vĩnh viễn và cả tính năng phát hiện
    # công ty ngừng hoạt động trở thành vô nghĩa.
    return True

def danh_muc_cho_place(db: Session, place: Place) -> list[str]:
    """Danh mục ngành nghề áp cho một địa điểm khi KHÔNG có params của job.

    Pha "kiểm tra lại" chạy ngoài mọi job nên không có `category_map` trong tay,
    nhưng nó vẫn ghi đè `place.category` bằng nhãn của trang chi tiết. Không tra
    được danh mục thì kết luận cũ nằm lại trên một cái nhãn không còn tồn tại —
    đúng lỗi đã gặp với "Fruit and vegetable wholesaler".

    Thứ tự: job gần nhất đã tìm ra nó (đúng bộ từ khoá nhất) -> bản dịch đã lưu
    cho quốc gia đó. Không có gì thì trả rỗng, và `cham` sẽ trả None = để nguyên.
    """
    ma = (place.country_code or "").upper()
    if not ma:
        return []
    from app.modules.keyword.entity import KeywordTranslation

    job_params = db.execute(
        select(ScrapeJob.params)
        .join(JobPlace, JobPlace.job_id == ScrapeJob.id)
        .where(JobPlace.place_id == place.id)
        .order_by(ScrapeJob.id.desc())
    ).scalars().all()
    for params in job_params:
        ds = ((params or {}).get("category_map") or {}).get(ma)
        if ds:
            return list(ds)

    row = db.execute(
        select(KeywordTranslation)
        .where(KeywordTranslation.country_code == ma)
        .order_by(KeywordTranslation.id.desc())
    ).scalars().first()
    return list(row.categories or []) if row else []

class PlaceWriter:
    def __init__(self, db: Session, region: str = "VN") -> None:
        self.db = db
        # `region` chỉ là PHƯƠNG ÁN DỰ PHÒNG cho địa điểm chưa biết quốc gia.
        # Vùng thật sự dùng để đọc số điện thoại nằm ở `region_of(place)`.
        self.region = region

    # ----- pha tìm kiếm -----
    def upsert_from_card(
        self,
        card: CardResult,
        job_id: int,
        keyword: str,
        country_code: str | None = None,
        cho_phep: list[str] | None = None,
        diem: str | None = None,
        nguon: str | None = None,
        ly_do: str | None = None,
    ) -> tuple[Place | None, bool]:
        """Tạo mới hoặc làm tươi một địa điểm từ thẻ kết quả.

        Trả `(None, False)` khi thẻ bị LOẠI vì ngoài danh mục ngành nghề — nơi gọi
        phải chịu được giá trị None này.

        `country_code` là quốc gia của TRUY VẤN đã tìm ra thẻ này (`JobQuery.gl`).
        `cho_phep` là danh mục ngành nghề của chính quốc gia đó; rỗng thì không lọc.

        `diem`/`nguon`/`ly_do` là quyết định đã có sẵn từ bên ngoài — dùng cho
        những thẻ vừa được AI phân xử. Truyền vào thì hàm này KHÔNG chấm lại:
        chấm hai lần với hai căn cứ khác nhau là cách chắc chắn nhất để `relevance`
        lưu một đằng còn `relevance_reason` giải thích một nẻo.

        Vì sao chặn ở ĐÂY chứ không lọc lúc đọc: Google độn thêm kết quả loãng dần
        khi hết kết quả khớp thật (đo được: 90% đúng ngành ở vị trí 1–20, còn 11%
        ở vị trí 101–118). Để chúng vào bảng rồi mới ẩn đi nghĩa là mọi câu đếm,
        mọi lần xuất file và mọi người sau này đọc bảng đều phải nhớ áp bộ lọc —
        quên một chỗ là rác lọt ra ngoài.

        Chặn KHÔNG IM LẶNG: mỗi thẻ bị loại để lại một dòng ở `place_rejects`
        (tên, ngành nghề, truy vấn) và được đếm vào `ScrapeJob.rejected_count`.
        Danh mục ngành nghề là phỏng đoán, nên phải có đường soi lại xem nó có
        đang siết quá tay không.
        """
        if not card.feature_id:
            # Không có định danh của Google thì không có cách khử trùng lặp đáng tin;
            # dùng URL làm khoá thay thế.
            key = card.maps_url.split("?", 1)[0]
        else:
            key = card.feature_id

        if diem is None:
            diem = cham_lien_quan(card.category, card.name, cho_phep)
            nguon = "rule" if diem is not None else None

        place = self.db.execute(select(Place).where(Place.feature_id == key)).scalar_one_or_none()
        is_new = place is None
        if place is None:
            if diem == NGHI_RAC:
                self._ghi_loai(job_id, keyword, key, card, nguon, ly_do)
                return None, False
            place = Place(feature_id=key, name=card.name, status=PLACE_PENDING)
            self.db.add(place)
        # Địa điểm ĐÃ CÓ trong bảng thì không loại, kể cả khi lượt này chấm là lạc
        # đề: nó đã lọt lưới của một job khác với danh mục khác, và xoá đi ở đây
        # là để job này quyết định thay cho job kia. Chỉ ghi nhận điểm.
        # Địa điểm đã `done` nhưng CHƯA từng mở trang chi tiết thì mở lại hàng đợi.
        #
        # Có đúng một đường sinh ra trạng thái đó: job bị HUỶ giữa chừng, và
        # `close_pending_of_job` chốt sổ những địa điểm còn dang dở bằng dữ liệu
        # của thẻ kết quả. Nếu không mở lại ở đây thì job sau nhặt đúng địa điểm
        # ấy, thấy nó đã `done`, và pha chi tiết bỏ qua — nó nằm lại VĨNH VIỄN
        # với địa chỉ rỗng và không có website, không lỗi, không dấu hiệu gì.
        #
        # Đã đo: huỷ job #2 giữa chừng rồi chạy job #7 trên cùng địa bàn, 6 địa
        # điểm ở trạng thái `done` / `detail_scraped=false` / `attempts=0` —
        # trông y hệt như đã quét xong.
        #
        # Không đụng tới địa điểm Google đã khẳng định đóng cửa vĩnh viễn:
        # `needs_detail` cố ý không mở chúng, mở lại hàng đợi chỉ tạo ra một vòng
        # quẩn pending -> done mỗi lần chạy.
        if (
            not is_new
            and place.status == PLACE_DONE
            and not place.detail_scraped
            and place.business_status != BUSINESS_CLOSED_PERM
        ):
            place.status = PLACE_PENDING

        truoc = place.relevance
        place.relevance = gop_lien_quan(truoc, diem)
        # Lý do chỉ đi kèm ĐÚNG cái điểm đã sinh ra nó. `gop` có thể giữ lại điểm
        # cũ (luật "chỉ nâng, không hạ"), và khi đó ghi đè lý do bằng lời giải
        # thích của lượt này là dán một câu giải thích lên một kết luận khác —
        # người đọc sau sẽ tin vào một thứ không hề đúng.
        if place.relevance != truoc or place.relevance_source is None:
            place.relevance_source = nguon
            place.relevance_reason = ly_do

        place.name = place.name or card.name
        place.cid = place.cid or card.cid
        place.maps_url = card.maps_url or place.maps_url
        place.lat = place.lat if place.lat is not None else card.lat
        place.lng = place.lng if place.lng is not None else card.lng
        place.category = place.category or card.category
        # Thẻ kết quả chỉ cho MẨU địa chỉ, và mẩu đó đi vào cột riêng.
        # KHÔNG bao giờ ghi vào `address`: cột đó chỉ dành cho địa chỉ đầy đủ từ
        # trang chi tiết. Trộn hai thứ vào một cột chính là gốc của chuyện "ô địa
        # chỉ lúc rỗng lúc hiện không đầy đủ".
        if card.address_short and not place.address_short:
            place.address_short = card.address_short
        # Quốc gia phải chốt TRƯỚC khi đọc số điện thoại ở dưới, và phải chốt SAU
        # khi đã gán lat/lng ở trên — toạ độ là nguồn mạnh thứ hai.
        nuoc_cu = place.country_code
        place.country_code, place.country_source = resolve_place_country(
            place.address or place.address_short,
            place.country_code,
            place.country_source,
            country_code,
            place.lat,
            place.lng,
        )
        if card.rating is not None:
            place.rating = card.rating
        if card.review_count is not None:
            place.review_count = card.review_count
        if card.business_status != "OPERATIONAL" or not place.business_status:
            place.business_status = card.business_status
        if card.hours_summary:
            place.hours_summary = card.hours_summary
            place.has_hours = place.has_hours or card.has_hours
        vung = region_of(place, self.region)
        if card.phone_raw and not place.phone_e164:
            place.phone_raw = card.phone_raw
            e164, national, valid = normalize_phone(card.phone_raw, vung)
            place.phone_e164, place.phone_national, place.phone_valid = e164, national, valid
        elif place.phone_raw and place.country_code != nuoc_cu:
            # Quốc gia VỪA ĐỔI trong chính lượt này (thường là toạ độ hoặc địa chỉ
            # đầy đủ vừa sửa lại một kết luận trước đó chỉ đoán theo `gl`).
            #
            # Không có nhánh này thì số đã lưu nằm im với vùng CŨ vĩnh viễn: điều
            # kiện `not place.phone_e164` ở trên khiến nó không bao giờ được đọc
            # lại. Và số đọc sai vùng vẫn "hợp lệ" — "081 882 1104" đọc theo VN ra
            # +84818821104 hợp lệ y như đọc theo TH ra +66818821104 — nên không có
            # bộ kiểm tra nào báo, nó chỉ lặng lẽ nằm trong file xuất cho sale gọi.
            e164, national, valid = normalize_phone(place.phone_raw, vung)
            place.phone_e164, place.phone_national, place.phone_valid = e164, national, valid

        place.last_seen_at = _now()
        # Tìm kiếm phải khớp cả hai: người dùng gõ tên đường thì mẩu từ thẻ
        # cũng phải ra, không chỉ địa chỉ đầy đủ.
        place.search_text = build_search_text(
            place.name, place.address or place.address_short, place.category
        )
        recompute_liveness(place)
        self.db.flush()

        self._link_keyword(place.id, keyword)
        self._link_job(job_id, place.id, is_new)
        self.db.commit()
        return place, is_new

    def _ghi_loai(
        self,
        job_id: int,
        keyword: str,
        key: str,
        card: CardResult,
        nguon: str | None = None,
        ly_do: str | None = None,
    ) -> None:
        """Ghi vết một thẻ bị loại, rồi commit ngay như đường ghi địa điểm.

        Cố ý KHÔNG khử trùng lặp: cùng một tiệm bánh bị loại ở ba truy vấn khác
        nhau thì ba dòng đó chính là bằng chứng bộ lọc đang chặn đúng cùng một
        thứ nhiều lần, không phải dữ liệu thừa.
        """
        self.db.add(
            PlaceReject(
                job_id=job_id,
                query=keyword[:300],
                feature_id=(card.feature_id or key)[:64],
                name=(card.name or "")[:300],
                category=(card.category or None),
                maps_url=card.maps_url,
                source=nguon,
                reason=ly_do,
            )
        )
        self.db.execute(
            update(ScrapeJob)
            .where(ScrapeJob.id == job_id)
            .values(rejected_count=ScrapeJob.rejected_count + 1)
        )
        self.db.commit()

    def _link_keyword(self, place_id: int, keyword: str) -> None:
        exists = self.db.execute(
            select(PlaceKeyword.id).where(
                PlaceKeyword.place_id == place_id, PlaceKeyword.keyword == keyword
            )
        ).first()
        if not exists:
            self.db.add(PlaceKeyword(place_id=place_id, keyword=keyword))
            self.db.flush()

    def _link_job(self, job_id: int, place_id: int, is_new: bool) -> None:
        exists = self.db.execute(
            select(JobPlace.id).where(JobPlace.job_id == job_id, JobPlace.place_id == place_id)
        ).first()
        if not exists:
            self.db.add(JobPlace(job_id=job_id, place_id=place_id, is_new=is_new))
            self.db.flush()

    # ----- pha chi tiết -----
    def apply_detail(
        self, place: Place, detail: DetailResult, cho_phep: list[str] | None = None
    ) -> Place:
        """Áp dữ liệu trang chi tiết lên một địa điểm đã có.

        `cho_phep` để CHẤM LẠI ngành nghề. Bắt buộc phải có, vì trang chi tiết
        ghi đè `place.category` — và nhãn ở đây thường khác hẳn nhãn trên thẻ kết
        quả, hay gặp nhất là thẻ ghi tiếng bản ngữ còn trang chi tiết ghi tiếng
        Anh. Không chấm lại thì kết luận cũ dựa trên một cái nhãn KHÔNG CÒN TỒN
        TẠI nữa.

        Đo thật: "AKR FRESH VEG & FROZEN FOODS PVT LTD" và "Rangat Bazar" đang
        mang nhãn "Fruit and vegetable wholesaler" — nằm thẳng trong danh mục —
        mà `relevance` vẫn là `weak`. Hai lead thật bị giấu khỏi bảng, và nhìn
        vào dữ liệu thì thấy mâu thuẫn không giải thích nổi.
        """
        if detail.name:
            place.name = detail.name
        if detail.address:
            # Đây là nguồn DUY NHẤT được phép ghi vào `address`.
            place.address = detail.address
        if detail.website:
            place.website = detail.website
            place.website_status = "UNCHECKED"
        if detail.category:
            place.category = detail.category
        if detail.rating is not None:
            place.rating = detail.rating
        if detail.review_count is not None:
            place.review_count = detail.review_count
        if detail.latest_review_days is not None:
            place.latest_review_days = detail.latest_review_days
        if detail.cid:
            place.cid = detail.cid
        if detail.lat is not None:
            place.lat = detail.lat
        if detail.lng is not None:
            place.lng = detail.lng
        place.business_status = detail.business_status
        place.hours_summary = detail.hours_summary or place.hours_summary
        place.has_hours = detail.has_hours or place.has_hours

        # Chốt lại quốc gia SAU khi đã có địa chỉ đầy đủ và toạ độ chính xác từ
        # trang chi tiết — đây là lúc dữ liệu đầy đủ nhất, nên kết luận ở đây
        # thường nâng cấp được bản suy từ `gl` lúc đọc thẻ.
        place.country_code, place.country_source = resolve_place_country(
            # `or address_short`: trang chi tiết không phải lúc nào cũng cho địa
            # chỉ. Bỏ qua mẩu từ thẻ ở đây là tự vứt một manh mối còn dùng được.
            place.address or place.address_short,
            place.country_code,
            place.country_source,
            self.region,
            place.lat,
            place.lng,
        )

        region = region_of(place, self.region)
        if detail.phone_raw:
            place.phone_raw = detail.phone_raw
            e164, national, valid = normalize_phone(detail.phone_raw, region)
            place.phone_e164, place.phone_national, place.phone_valid = e164, national, valid
        elif place.phone_raw:
            # Trang chi tiết không cho SĐT mới, nhưng quốc gia có thể VỪA đổi ngay
            # ở trên (thẻ kết quả chỉ đoán theo `gl`, giờ mới có địa chỉ đầy đủ).
            # Không đọc lại thì số đã phân tích sai vùng sẽ nằm lại vĩnh viễn.
            e164, national, valid = normalize_phone(place.phone_raw, region)
            place.phone_e164, place.phone_national, place.phone_valid = e164, national, valid

        place.missing_fields = list(detail.missing_fields)
        place.detail_scraped = True
        place.status = PLACE_DONE
        place.attempts += 1
        place.last_error = None
        place.scraped_at = _now()
        place.last_verified_at = _now()
        diem = cham_lien_quan(place.category, place.name, cho_phep)
        truoc = place.relevance
        # CHỈ ĐƯỢC NÂNG (`gop`), không được hạ. Trang chi tiết biết rõ hơn về
        # NHÃN, nhưng thẻ kết quả mới là thứ đã qua đủ bước phân xử — kể cả lượt
        # AI xem tên. Cho phép hạ ở đây thì một phán quyết "giữ" của AI bị một
        # cái nhãn chung chung xoá mất, mà không ai thấy.
        place.relevance = gop_lien_quan(truoc, diem)
        if place.relevance != truoc:
            place.relevance_source, place.relevance_reason = "rule", None
        place.search_text = build_search_text(
            place.name, place.address or place.address_short, place.category
        )
        recompute_liveness(place)
        self.db.commit()
        return place

    def finish_without_detail(self, place: Place) -> Place:
        """Kết thúc địa điểm chỉ với dữ liệu từ thẻ (không cần mở trang chi tiết)."""
        place.status = PLACE_DONE
        place.scraped_at = place.scraped_at or _now()
        place.last_verified_at = _now()
        if not place.website:
            place.website_status = "NONE"
        recompute_liveness(place)
        self.db.commit()
        return place

    def mark_failed(self, place: Place, error: str) -> None:
        place.status = PLACE_FAILED
        place.attempts += 1
        place.last_error = error[:500]
        self.db.commit()

    def apply_website_status(self, place: Place, status: str) -> None:
        place.website_status = status
        place.website_checked_at = _now()
        recompute_liveness(place)
        self.db.commit()

    # ----- truy vấn phục vụ worker -----
    def next_pending_of_job(self, job_id: int) -> Place | None:
        """Lấy MỘT địa điểm còn chờ xử lý.

        Vòng lặp pha chi tiết gọi hàm này mỗi lượt. Nạp cả danh sách (có thể hàng
        chục nghìn dòng) rồi chỉ dùng phần tử đầu sẽ biến vòng lặp thành O(n^2).
        """
        return (
            self.db.execute(
                select(Place)
                .join(JobPlace, JobPlace.place_id == Place.id)
                .where(JobPlace.job_id == job_id, Place.status == PLACE_PENDING)
                .order_by(Place.id)
                .limit(1)
            )
            .scalars()
            .first()
        )

    def close_pending_of_job(self, job_id: int) -> int:
        """Chốt sổ những địa điểm còn dang dở của một job vừa bị HUỶ.

        Huỷ job phải có nghĩa là NGỪNG tiêu request vào nó. Nhưng địa điểm mà
        pha tìm kiếm đã tạo ra vẫn nằm ở `pending`, và vòng `run_sweep_phase`
        (vốn sinh ra cho nút "Kiểm tra lại") sẽ nhặt đúng chúng lên quét tiếp —
        tức là nút Huỷ bị vô hiệu hoá một cách âm thầm. Đã thấy thật: huỷ job
        #617 xong worker vẫn đi mở 46 trang chi tiết của nó.

        Chốt bằng dữ liệu đang có (`finish_without_detail`) chứ không xoá: thẻ
        kết quả đã cho tên, vị trí, SĐT — vứt đi là phí một lượt quét đã trả tiền.
        """
        so = 0
        for place in self.places_of_job(job_id, (PLACE_PENDING,)):
            self.finish_without_detail(place)
            so += 1
        return so

    def next_pending_anywhere(self) -> Place | None:
        """Một địa điểm đang `pending` bất kể thuộc job nào.

        Phục vụ nút "Kiểm tra lại": nó đặt địa điểm về `pending`, nhưng pha chi
        tiết chỉ lấy việc qua `next_pending_of_job(job_id)` — tức chỉ trong job
        ĐANG CHẠY. Địa điểm thuộc một job đã `done` thì không worker nào đụng
        tới, nằm ở `pending` vĩnh viễn, và cái toast "đã đưa vào hàng chờ" là
        lời hứa suông.
        """
        return (
            self.db.execute(
                select(Place)
                .where(Place.status == PLACE_PENDING)
                .order_by(Place.id)
                .limit(1)
            )
            .scalars()
            .first()
        )

    def count_pending_anywhere(self) -> int:
        return int(
            self.db.execute(
                select(func.count()).where(Place.status == PLACE_PENDING)
            ).scalar_one()
        )

    def bo_vi_thieu_sdt(self, place: Place, job_id: int, keyword: str) -> bool:
        """Xoá một địa điểm không có số điện thoại. Trả True nếu đã xoá.

        SỐ ĐIỆN THOẠI LÀ THỨ DUY NHẤT DÙNG ĐƯỢC ở đây: không có nó thì không gọi
        được, và một dòng không gọi được chỉ làm loãng danh sách của sale.

        Gọi SAU pha chi tiết, không phải lúc đọc thẻ. Đo thật trên 203 địa điểm:
        84 có số ngay từ thẻ, nhưng 119 chỉ lộ số sau khi mở trang chi tiết.
        Chặn ở thẻ là vứt oan đúng 119 lead thật đó.

        XOÁ chứ không chỉ ẩn, vì người dùng yêu cầu rõ "không cần ghi vào dữ
        liệu". Không mất dấu: một dòng `place_rejects` giữ lại tên, ngành nghề,
        truy vấn và link Maps — đủ để soi lại hoặc quét lại thủ công.

        Chỉ xoá khi THẬT SỰ không có số. Địa điểm đã có số thì không bao giờ rơi
        vào đây, nên một job sau không thể xoá mất lead của job trước.
        """
        if place.phone_e164:
            return False
        self.db.add(
            PlaceReject(
                job_id=job_id,
                query=keyword[:300],
                feature_id=(place.feature_id or "")[:64],
                name=(place.name or "")[:300],
                category=place.category,
                maps_url=place.maps_url,
                source="rule",
                reason="Không có số điện thoại",
            )
        )
        self.db.execute(
            update(ScrapeJob)
            .where(ScrapeJob.id == job_id)
            .values(rejected_count=ScrapeJob.rejected_count + 1)
        )
        # Xoá địa điểm kéo theo `job_places` và `place_keywords` bằng cascade của
        # khoá ngoại. Bộ đếm của job KHÔNG hỏng theo: `count_places_of_job` và
        # `count_done_places_of_job` đếm lại từ DB mỗi lần chứ không cộng dồn.
        self.db.delete(place)
        self.db.commit()
        return True

    def count_places_of_job(self, job_id: int) -> int:
        return int(
            self.db.execute(
                select(func.count()).where(JobPlace.job_id == job_id)
            ).scalar_one()
        )

    def count_done_places_of_job(self, job_id: int) -> int:
        """Số địa điểm của job đã ở trạng thái `done`.

        Không dùng bộ đếm cộng dồn: một địa điểm có thể đã `done` từ job TRƯỚC
        (trùng giữa hai từ khoá/hai lần chạy) nên vòng lặp pha chi tiết không hề
        chạm tới nó. Cộng dồn sẽ cho "3/4" trong khi job đã xong 100%.
        """
        return int(
            self.db.execute(
                select(func.count())
                .join(Place, Place.id == JobPlace.place_id)
                .where(JobPlace.job_id == job_id, Place.status == PLACE_DONE)
            ).scalar_one()
        )

    def places_of_job(self, job_id: int, statuses: tuple[str, ...] = (PLACE_PENDING,)) -> list[Place]:
        return list(
            self.db.execute(
                select(Place)
                .join(JobPlace, JobPlace.place_id == Place.id)
                .where(JobPlace.job_id == job_id, Place.status.in_(statuses))
                .order_by(Place.id)
            )
            .scalars()
            .all()
        )

    def places_needing_website_check(self, job_id: int) -> list[Place]:
        return list(
            self.db.execute(
                select(Place)
                .join(JobPlace, JobPlace.place_id == Place.id)
                .where(
                    JobPlace.job_id == job_id,
                    Place.website.isnot(None),
                    Place.website_status.in_(("UNCHECKED", "NONE")),
                )
                .order_by(Place.id)
            )
            .scalars()
            .all()
        )
