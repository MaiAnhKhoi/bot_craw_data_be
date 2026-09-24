from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PlaceReject(Base):
    """Nhật ký những thẻ kết quả bị bộ lọc ngành nghề loại ngay lúc ghi.

    VÌ SAO TỒN TẠI: để việc loại bỏ KHÔNG BAO GIỜ IM LẶNG.

    Bộ lọc ngành nghề là một PHỎNG ĐOÁN — nó so tên ngành Google gắn cho địa
    điểm với danh mục ngành do AI liệt kê cho nước đó. Phỏng đoán sai là chuyện
    sẽ xảy ra: AI quên một ngành, Google gắn nhãn chung chung, doanh nghiệp đặt
    tên trung tính. Phỏng đoán sai mà không để lại dấu vết nào thì người dùng
    mất lead thật mà không có cách nào phát hiện — đúng kiểu hỏng âm thầm.
    Đã đo: job "Công ty trái cây" ở quần đảo Andaman ra 174 địa điểm, riêng truy
    vấn `produce supplier` trả 118 kết quả thì 51% lạc đề. Số bị loại đủ lớn để
    một lần siết quá tay cũng đủ lớn.

    Những dòng ở đây CỐ Ý KHÔNG nằm trong bảng `places`: chúng không lọt vào
    bảng chính, không lọt vào file xuất, không được tính là kết quả của job.
    Chúng chỉ để soi lại khi nghi bộ lọc siết quá tay.

    Vì thế nó giữ TỐI THIỂU — tên + ngành nghề + truy vấn đã tìm ra nó, đủ để
    nhìn một phát biết "cái này đáng ra phải giữ". Không có số điện thoại, không
    có địa chỉ: đó là dữ liệu của lead, mà đây không phải danh sách lead.
    """

    __tablename__ = "place_rejects"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Xoá job là xoá sạch nhật ký loại bỏ của nó: mấy dòng này chỉ có nghĩa khi
    # còn job để đối chiếu, để lại thì thành rác không ai truy ngược được.
    job_id: Mapped[int] = mapped_column(ForeignKey("scrape_jobs.id", ondelete="CASCADE"), index=True)
    # Truy vấn đã tìm ra nó. Đây mới là cột dùng để chẩn đoán: loại rải đều khắp
    # các truy vấn là bộ lọc hẹp quá, còn dồn hết vào một truy vấn thì chính truy
    # vấn đó dịch sai hoặc quá rộng.
    query: Mapped[str] = mapped_column(String(300))
    # Có thể trống: thẻ kết quả nào Google không kèm `feature_id` thì vẫn phải
    # ghi lại, thà thiếu định danh còn hơn mất luôn dấu vết.
    feature_id: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(300))
    # Chính là căn cứ để loại. Trống nghĩa là Google chưa gắn nhãn ngành.
    category: Mapped[str | None] = mapped_column(String(160))
    # Để mở thẳng địa điểm trên Google Maps mà kiểm bằng mắt — rẻ hơn nhiều so
    # với lưu thêm trường rồi phải bảo trì.
    maps_url: Mapped[str | None] = mapped_column(Text)
    # AI hay LUẬT CỨNG đã loại dòng này: 'rule' | 'ai' | NULL (loại trước khi có cột).
    #
    # Cả bảng này sinh ra để việc loại bỏ không im lặng, mà "đã loại" thôi thì chưa đủ
    # im lặng bớt đi bao nhiêu. Luật cứng loại vì nhãn ngành không nằm trong danh mục —
    # sai kiểu máy móc, sửa bằng cách bổ sung danh mục. AI loại vì nó tự phán trên tên
    # + nhãn + địa chỉ — sai kiểu khác hẳn, sửa bằng cách sửa prompt. Trộn hai nguồn
    # vào một đống thì nhìn 96 dòng bị loại cũng không biết phải đi sửa chỗ nào.
    source: Mapped[str | None] = mapped_column(String(8))
    # Lý do loại, bằng lời — CHỈ có khi `source` là 'ai'.
    #
    # Đây là thứ biến bảng này từ "danh sách đã vứt" thành thứ phán được. Người dùng
    # mở ra là để trả lời MỘT câu: bộ lọc có siết quá tay không. Nhìn tên với nhãn
    # ngành thì phải tự đoán vì sao nó bị vứt; đọc được câu "tên có chữ `fruit` nhưng
    # nhãn `Dress store`, là tiệm quần áo" thì gật hay lắc đầu ngay được.
    reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
