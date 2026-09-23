# Chống chặn IP / CAPTCHA — khi chạy 1 luồng trên IP công ty

Bối cảnh: worker chạy trên một máy đặt tại văn phòng, đi ra internet bằng **IP của
công ty**, **không có proxy**. Không có IP nào để xoay, nên thứ duy nhất điều khiển
được là **tốc độ và hành vi**. Toàn bộ chiến lược dưới đây xoay quanh điều đó.

## Thứ tự đòn bẩy (làm từ trên xuống)

| # | Biện pháp | Cài ở đâu | Vì sao hiệu quả |
|---|---|---|---|
| 1 | **Chạy chậm, đều** — nghỉ 4–8 giây giữa hai trang | `pace_min_seconds` / `pace_max_seconds` | Đây là yếu tố số một. Google không chặn vì bạn là bot, mà vì bạn tạo tải bất thường trên một IP. ~8 trang/phút nằm sâu dưới ngưỡng của một IP doanh nghiệp bình thường. |
| 2 | **Không mở trang chi tiết khi không cần** | `detail_mode`, `needs_detail()` | Ít request hơn = ít rủi ro hơn. Thẻ kết quả đã cho sẵn tên, SĐT, danh mục, địa chỉ rút gọn, rating, trạng thái — chỉ website mới bắt buộc mở trang chi tiết. |
| 3 | **Bỏ qua địa điểm đã quét gần đây** (TTL) | `ttl_days` | Lần chạy thứ hai trở đi giảm 60–80% request. |
| 4 | **Nhịp tự điều chỉnh** | `engine/pacing.py` | Có dấu hiệu bị soi (timeout lạ, trang rỗng) → tự nhân đôi thời gian nghỉ; chạy êm 200 trang → nới lại dần. Máy tự dò ngưỡng an toàn thay vì bạn phải đoán. |
| 5 | **Cầu dao ngắt mạch** | `_handle_block()` | Gặp `/sorry/` hoặc captcha → nghỉ hẳn 1 giờ, trả job về hàng đợi, KHÔNG đánh dấu thất bại. Tiến độ đã lưu, tự chạy tiếp sau đó — không cần người can thiệp. |
| 6 | **Nghỉ đêm** | `night_rest_start/end` (mặc định 2h–6h) | Một IP văn phòng hoạt động lúc 3 giờ sáng là bất thường. |
| 7 | **Hồ sơ trình duyệt bền** | `browser_profile_dir` | Giữ cookie/consent suốt nhiều tuần: phiên trông như một người dùng quen, không phải khách lạ mới toanh mỗi lần mở. |
| 8 | **Chặn ảnh/font/video + miền quảng cáo** | `browser.py::new_page` | Giảm 60–70% băng thông và rút ngắn thời gian tải — ít request phụ hơn cũng là ít dấu vết hơn. |
| 9 | **Cuộn như người** | `human_scroll()` | Lăn từng nấc có nghỉ, thay vì nhảy thẳng tới đáy danh sách. |
| 10 | **Xoá dấu vết tự động hoá** | `_STEALTH_JS`, `BCD_BROWSER_ENGINE=patchright` | `navigator.webdriver`, cờ `--enable-automation`, ngôn ngữ/múi giờ khớp Việt Nam. Patchright vá thêm ở tầng CDP. |

## Cố ý KHÔNG làm

- **Không giải CAPTCHA.** Gặp captcha là tín hiệu "bạn đang đi quá nhanh", không
  phải chướng ngại cần phá. Giải nó bằng dịch vụ ngoài vừa tốn tiền, vừa không ổn
  định, vừa đẩy quan hệ với Google sang mức đối đầu. Cách bền là chậm lại.
- **Không chạy nhiều luồng trên cùng một IP.** Tăng số context trên một IP làm
  tăng tỉ lệ bị chặn mà không tăng sản lượng thực. Muốn nhanh hơn thì thêm **máy
  và IP khác** — kiến trúc đã sẵn sàng (hàng đợi nằm trong Postgres,
  `FOR UPDATE SKIP LOCKED`).

## Khi nào cần đổi chiến lược

| Dấu hiệu | Xử lý |
|---|---|
| `blocked_today` > 0 vài ngày liên tiếp | Tăng `pace_min/max_seconds`, giảm giờ chạy |
| Bị chặn ngay khi vừa khởi động | IP đã bị đánh dấu — nghỉ 24h; nếu tái diễn thì cân nhắc 1 port proxy 4G |
| Độ đầy đủ cột SĐT rơi đột ngột | Không phải bị chặn mà là **Google đổi DOM** — chạy `pytest -m live`, sửa selector trong `engine/detail.py` và `engine/search.py` |

## Sản lượng kỳ vọng (đo thực tế)

- Pha tìm kiếm: ~10–20 giây cho một truy vấn (70–100 kết quả), đã kèm sẵn SĐT.
- Pha chi tiết: ~2 giây/trang + thời gian nghỉ → **~8 địa điểm/phút**.
- Chạy 20 giờ/ngày → **~9.500 địa điểm/ngày**, khoảng **280.000/tháng**.
