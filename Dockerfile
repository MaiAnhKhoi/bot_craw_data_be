# =====================================================================
# bot_craw_data — MỘT image, BA vai trò (chỉ khác `command`):
#   api      uvicorn app.main:app --host 0.0.0.0 --port 8000   (mặc định, xem CMD cuối file)
#   worker   python -m app.workers.runner                      tiến trình cào, CHỈ 1 BẢN
#   migrate  alembic upgrade head                              chạy MỘT LẦN rồi thoát
#
# ★ VÌ SAO KHÔNG PHẢI `python:3.12-slim` RỒI `pip install playwright`:
# Chromium không phải một file .exe độc lập — nó cần vài chục thư viện hệ thống (libnss3,
# libnspr4, libatk*, libcups2, libdrm2, libgbm1, libasound2, libxkbcommon...) cộng bộ font.
# Trên `slim`, `pip install playwright` tải được trình duyệt nhưng thiếu các thư viện đó:
# `launch()` chết bằng "error while loading shared libraries", hoặc tệ hơn là chạy được mà
# mọi trang render ô vuông ▯▯▯ vì không có font — lúc ấy parser đọc ra chuỗi rỗng và ta
# đi tìm bug trong selector suốt một ngày. Image này của Microsoft đã có sẵn tất cả, kèm
# Chromium ĐÚNG revision cho playwright 1.63 nằm ở /ms-playwright (PLAYWRIGHT_BROWSERS_PATH).
#
# ⚠️ Tag phải là `v1.63.0-noble` (ba số). `v1.63-noble` KHÔNG tồn tại trên registry.
# Đổi tag này thì phải đổi trần ghim `playwright>=1.63,<1.64` trong requirements.txt ở CÙNG
# một commit: lệch phiên bản = "Executable doesn't exist at /ms-playwright/chromium-XXXX"
# lúc chạy, build vẫn xanh.
# =====================================================================
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

# PYTHONUNBUFFERED: không có dòng này thì stdout bị đệm 4KB, `docker logs` im lặng hàng phút
#   rồi đổ ra một cục — đúng lúc cần theo dõi con bot đang cào tới đâu thì không thấy gì.
# PIP_NO_CACHE_DIR: cache wheel chỉ làm phình layer, image không bao giờ cài lại pip.
# TZ: log, giờ nghỉ đêm (night_rest_start/end) và dấu thời gian job đều tính theo giờ VN.
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1
ENV TZ=Asia/Ho_Chi_Minh

WORKDIR /code

# Cài dependency TRƯỚC khi COPY code: sửa một dòng trong app/ thì Docker vẫn dùng lại layer
# pip đã cache (tiết kiệm vài phút mỗi lần build). Đảo thứ tự là cài lại toàn bộ mỗi lần.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# ★ Chạy bằng user thường. `pwuser` (uid 1001) có sẵn trong image nền và đã được cấp quyền
# đọc /ms-playwright — tự tạo user mới thì phải tự chmod lại chỗ đó.
#
# KHÁC eco_backend ở chỗ: container này CÓ ghi xuống đĩa. Hồ sơ trình duyệt
# (var/browser_profile — cookie + trạng thái consent của Google, mất là bị bắt bấm lại từ
# đầu và trông giống máy lạ), file xuất (var/exports) và log đều nằm trong `var/`. Thư mục
# phải tồn tại VÀ thuộc về pwuser TRƯỚC khi đổi user: Chromium không tạo nổi profile thì
# `launch_persistent_context` ném EACCES ngay lần cào đầu tiên.
RUN mkdir -p var/browser_profile var/exports var/logs && chown -R pwuser:pwuser /code
USER pwuser

EXPOSE 8000

# ★ CMD KHÔNG chạy `alembic upgrade head`.
#
# Kiểu cũ `alembic upgrade head && uvicorn ...` chỉ đúng khi có ĐÚNG MỘT container. Ở đây
# `api` và `worker` dùng CHUNG image này và khởi động CÙNG LÚC: cả hai cùng đọc
# `alembic_version` thấy cùng một bản, cùng chạy cùng một migration, bản thua chết bằng
# `DuplicateTable` rồi được Docker khởi động lại — lần này không còn gì để làm nên "tự
# khỏi". Hỏng kiểu tự khỏi là hỏng khó chẩn đoán nhất: lúc người ta nhìn vào thì mọi thứ
# đã xanh, chỉ còn một dòng traceback trong log của lần chạy trước.
#
# Migration là JOB CHẠY TRƯỚC (service `migrate` trong cả hai file compose); `api`/`worker`
# chỉ khởi động khi job đó `exit 0` (`service_completed_successfully`). Chạy image này bằng
# tay thì nhớ migrate trước:
#     docker run --rm botcraw-be alembic upgrade head
#
# Một worker uvicorn là đủ: đây là công cụ nội bộ, vài người dùng, và phần nặng nằm ở tiến
# trình `worker` chứ không ở API. Thêm `--workers N` chỉ nhân bản kết nối DB một cách vô ích.
#
# ⚠️ `worker` KHÔNG được chạy nhiều bản: một Chromium, một BrowserContext, một IP công ty.
# Hai con bot cùng cào từ một IP là cách nhanh nhất để ăn CAPTCHA cho cả văn phòng.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
