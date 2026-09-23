"""Nhịp cào tự điều chỉnh + cầu dao ngắt mạch.

Đây là lớp chống chặn quan trọng nhất khi chạy 1 luồng trên IP công ty: không có
proxy để xoay, nên thứ duy nhất ta điều khiển được là TỐC ĐỘ.

Quy tắc:
- Chạy êm đủ lâu  -> nới nhịp về mức nền (nhưng không bao giờ nhanh hơn nền).
- Có dấu hiệu bị soi (timeout lạ, trang rỗng) -> chậm lại gấp đôi.
- Bị chặn thật (trang /sorry/, captcha) -> nghỉ dài rồi mới thử lại.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Pacer:
    base_min: float = 4.0
    base_max: float = 8.0
    ceiling: float = 45.0
    probe_after: int = 200          # số trang êm liên tiếp thì nới nhịp một bậc
    multiplier: float = 1.0
    consecutive_ok: int = 0

    def _bounds(self) -> tuple[float, float]:
        return (
            min(self.base_min * self.multiplier, self.ceiling),
            min(self.base_max * self.multiplier, self.ceiling),
        )

    @property
    def current_seconds(self) -> float:
        """Nhịp thật đang áp dụng — PHẢI kẹp theo `ceiling` giống hệt `wait()`.

        Con số này đi thẳng lên log và API trạng thái worker; nếu không kẹp thì
        giao diện báo một đằng mà worker ngủ một nẻo.
        """
        lo, hi = self._bounds()
        return round((lo + hi) / 2, 1)

    async def wait(self) -> None:
        lo, hi = self._bounds()
        await asyncio.sleep(random.uniform(lo, hi))

    def on_success(self) -> None:
        self.consecutive_ok += 1
        if self.multiplier > 1.0 and self.consecutive_ok >= self.probe_after:
            # Nới từ từ, không nhảy thẳng về mức nền.
            self.multiplier = max(1.0, self.multiplier / 1.5)
            self.consecutive_ok = 0

    def on_suspicion(self) -> None:
        """Chưa bị chặn hẳn nhưng có mùi: chậm lại ngay."""
        self.consecutive_ok = 0
        self.multiplier = min(self.multiplier * 2, self.ceiling / max(self.base_min, 0.1))

    def on_block(self) -> None:
        self.consecutive_ok = 0
        self.multiplier = min(self.multiplier * 3, self.ceiling / max(self.base_min, 0.1))

    def reset(self) -> None:
        self.multiplier = 1.0
        self.consecutive_ok = 0


def in_night_rest(now: datetime, start_hour: int, end_hour: int) -> bool:
    """Có đang trong khung giờ nghỉ đêm không (start == end nghĩa là tắt tính năng)."""
    if start_hour == end_hour:
        return False
    hour = now.hour
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour   # khung vắt qua nửa đêm
