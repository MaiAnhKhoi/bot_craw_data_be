"""Chặn dò mật khẩu: khoá tạm theo TÀI KHOẢN và theo ĐỊA CHỈ IP.

Vì sao cần, bằng số đo thật trên chính hệ thống này: bcrypt đã làm chậm sẵn, còn
khoảng 20 lần thử mỗi giây. Mật khẩu ngẫu nhiên 8 ký tự thường cần ~325 năm nên
vốn đã an toàn — nhưng mật khẩu kiểu `sale2026`, `Ago@2026` nằm trong vài nghìn
cái phổ biến đầu tiên, tức là rụng trong DƯỚI 5 PHÚT. Nhân viên sẽ đặt đúng kiểu
đó, và không có luật nào ép được họ đặt khác mà không làm họ ghi mật khẩu ra
giấy dán màn hình.

HAI TẦNG, mỗi tầng chặn một kiểu tấn công khác nhau:

  * Theo TÀI KHOẢN — chặn kiểu dò một người: thử nghìn mật khẩu vào `admin`.
  * Theo IP — chặn kiểu rải: thử vài mật khẩu phổ biến vào TẤT CẢ tài khoản.
    Tầng tài khoản một mình không bắt được, vì mỗi tài khoản chỉ sai 2-3 lần.

Trạng thái nằm trong DATABASE chứ không phải bộ nhớ tiến trình. Ba lý do, lý do
cuối là quyết định: API có thể chạy nhiều tiến trình; khởi động lại không được
xoá sạch cái đang chặn; và quản trị phải NHÌN THẤY ai đang bị khoá để mở ra.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.config import get_settings
from app.core.database import Base
from app.modules.identity.entity import User


class LoginIpBlock(Base):
    """Đếm số lần đăng nhập hỏng theo địa chỉ IP.

    Bảng riêng chứ không nhét vào `users`: một IP rải thử lên nhiều tài khoản,
    và có khi lên cả tài khoản KHÔNG TỒN TẠI — không có dòng `users` nào để ghi.
    """

    __tablename__ = "login_ip_blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    ip: Mapped[str] = mapped_column(String(45), unique=True, index=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def _now() -> datetime:
    return datetime.now(UTC)


def con_khoa(moc: datetime | None) -> int:
    """Số PHÚT còn lại của một lần khoá. 0 nghĩa là không còn bị khoá.

    Làm tròn LÊN: còn 10 giây mà báo "0 phút" thì người dùng bấm lại ngay và
    nhận đúng lỗi đó, tưởng hệ thống hỏng.
    """
    if moc is None:
        return 0
    con_lai = (moc - _now()).total_seconds()
    return 0 if con_lai <= 0 else max(1, int(con_lai // 60) + (1 if con_lai % 60 else 0))


class LockoutGuard:
    """Gác cửa đăng nhập. Không tự commit — nơi gọi quyết định lúc nào lưu."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.s = get_settings()

    # ---------- kiểm trước khi cho thử mật khẩu ----------

    def phut_con_khoa(self, user: User | None, ip: str | None) -> int:
        """Số phút còn bị chặn, xét cả hai tầng. 0 = cho thử.

        Kiểm TRƯỚC khi so mật khẩu, và kiểm IP trước tài khoản: người đang bị
        chặn theo IP thì dù gõ đúng mật khẩu cũng không được vào, nên so mật
        khẩu chỉ tốn thêm 250 ms bcrypt cho mỗi lần tấn công.
        """
        if ip:
            row = self._dong_ip(ip, tao_moi=False)
            if row is not None and (phut := con_khoa(row.locked_until)):
                return phut
        if user is not None and (phut := con_khoa(user.locked_until)):
            return phut
        return 0

    # ---------- ghi nhận kết quả ----------

    def ghi_that_bai(self, user: User | None, ip: str | None) -> None:
        """Đếm thêm một lần sai, khoá nếu chạm ngưỡng.

        `user` có thể là None — gõ tên đăng nhập không tồn tại VẪN phải tính vào
        tầng IP. Không tính thì kẻ tấn công chỉ cần đổi tên đăng nhập mỗi lần là
        thoát sạch tầng này.
        """
        moc = _now() + timedelta(minutes=self.s.login_lock_minutes)
        if user is not None:
            user.failed_attempts = (user.failed_attempts or 0) + 1
            if user.failed_attempts >= self.s.login_max_attempts:
                user.locked_until = moc
        if ip:
            row = self._dong_ip(ip, tao_moi=True)
            row.failed_attempts = (row.failed_attempts or 0) + 1
            if row.failed_attempts >= self.s.login_ip_max_attempts:
                row.locked_until = moc

    def ghi_thanh_cong(self, user: User, ip: str | None) -> None:
        """Đăng nhập đúng thì xoá sạch bộ đếm của cả hai tầng.

        Xoá cả tầng IP là có chủ đích: một người trong phòng đăng nhập được
        chứng minh đây không phải IP của kẻ tấn công. Không xoá thì mọi lần gõ
        nhầm trong ngày cộng dồn lại và tới chiều cả phòng bị khoá.
        """
        user.failed_attempts = 0
        user.locked_until = None
        if ip:
            row = self._dong_ip(ip, tao_moi=False)
            if row is not None:
                row.failed_attempts = 0
                row.locked_until = None

    def mo_khoa(self, user: User) -> None:
        user.failed_attempts = 0
        user.locked_until = None

    # ---------- nội bộ ----------

    def _dong_ip(self, ip: str, tao_moi: bool) -> LoginIpBlock | None:
        row = self.db.execute(
            select(LoginIpBlock).where(LoginIpBlock.ip == ip)
        ).scalar_one_or_none()
        if row is None and tao_moi:
            row = LoginIpBlock(ip=ip[:45], failed_attempts=0)
            self.db.add(row)
            self.db.flush()
        return row


def dia_chi_that(request) -> str | None:  # noqa: ANN001 — starlette Request
    """Địa chỉ IP của người gọi, có tính tới proxy đứng trước.

    Hệ thống này chạy sau Cloudflare Tunnel và sau container `web` của Next, nên
    `request.client.host` luôn là IP của proxy — dùng thẳng nó là chặn cả thế
    giới vào cùng một rổ, và một kẻ tấn công sẽ khoá được toàn bộ người dùng.

    `CF-Connecting-IP` do chính Cloudflare đặt và GHI ĐÈ mọi giá trị người gọi
    tự gửi lên, nên tin được khi đi qua tunnel. `X-Forwarded-For` thì KHÔNG tin
    được nói chung (ai cũng tự đặt được), nhưng ở đây chuỗi proxy nằm trọn trong
    mạng Docker của mình nên vẫn dùng — và hậu quả xấu nhất nếu ai đó giả mạo nó
    chỉ là họ tự thoát khỏi tầng chặn theo IP, chứ không chặn được người khác.
    """
    if request is None:
        return None
    for ten in ("cf-connecting-ip", "x-forwarded-for"):
        raw = request.headers.get(ten)
        if raw:
            # `X-Forwarded-For` là chuỗi "client, proxy1, proxy2" — phần tử ĐẦU
            # mới là người gọi thật.
            return raw.split(",")[0].strip()[:45] or None
    return request.client.host if request.client else None
