# Phát hiện công ty đã ngừng hoạt động

Danh bạ Google Maps đầy hồ sơ của doanh nghiệp đã nghỉ từ lâu. Gọi vào đó là đốt
thời gian của sale. Hệ thống gom nhiều tín hiệu rẻ tiền thành một **điểm sống/chết**
(0–100) kèm **lý do**, để sale biết nên gọi ai trước.

Cài đặt: `app/modules/scraper/engine/liveness.py` (hàm thuần, có unit test).

## Tín hiệu và điểm trừ

| Mã lý do | Trừ | Nguồn | Độ tin cậy |
|---|---|---|---|
| `CLOSED_PERMANENTLY` | **100** | Google ghi nhận "Đã đóng cửa vĩnh viễn" | Khẳng định — kết luận luôn, không xét gì thêm |
| `CLOSED_TEMPORARILY` | 45 | "Tạm thời đóng cửa" | Khẳng định |
| `WEBSITE_DEAD` | 25 | httpx: DNS hỏng, 5xx, 404 | Mạnh |
| `WEBSITE_PARKED` | 20 | Trang đỗ tên miền / rao bán / gần như trống | Mạnh |
| `REVIEWS_STALE_3Y` | 28 | Đánh giá mới nhất ≥ 3 năm | Mạnh |
| `REVIEWS_STALE_2Y` | 16 | ≥ 2 năm | Vừa |
| `REVIEWS_STALE_1Y` | 6 | ≥ 1 năm | Yếu |
| `NO_REVIEWS` | 20 | Chưa có đánh giá nào | Vừa |
| `FEW_REVIEWS` | 8 | Dưới 3 đánh giá | Yếu |
| `NO_PHONE` | 15 | Không có SĐT | Vừa |
| `INVALID_PHONE` | 10 | libphonenumber báo không hợp lệ | Vừa |
| `NO_HOURS` | 10 | Không công bố giờ mở cửa | Yếu |
| `NO_WEBSITE` | 5 | Không có website | Rất yếu (nhiều hộ kinh doanh không có web) |

**Nhãn**: `ACTIVE` ≥ 70 · `SUSPECT` 40–69 · `DEAD` < 40.

## Vì sao "tuổi đánh giá" là tín hiệu mạnh nhất sau cờ của Google

Quán còn bán thì khách vẫn để lại đánh giá. Một cửa hàng có 30 đánh giá nhưng cái
mới nhất từ 3 năm trước gần như chắc chắn đã nghỉ, dù Google chưa gắn cờ. Ta đọc
luôn các dòng "2 năm trước" hiển thị sẵn trong khung chi tiết — **không cần bấm
sang tab đánh giá**, nên không tốn thêm request nào.

Chỉ xét tín hiệu này khi thật sự có đánh giá: nơi chưa ai đánh giá thì `NO_REVIEWS`
đã nói lên điều đó rồi, không được trừ hai lần.

## Kiểm tra website bằng httpx, không dùng trình duyệt

Chạy ở pha thứ ba của job, song song 8 kết nối, không đụng tới Google nên không
làm tăng rủi ro bị chặn. Phân loại:

- `OK` — 2xx/3xx và nội dung không giống trang đỗ. 401/403 vẫn coi là OK (tường lửa,
  không phải doanh nghiệp chết).
- `DEAD` — DNS hỏng, timeout, 5xx, 404/410.
- `PARKED` — khớp mẫu "rao bán tên miền", "coming soon", "Welcome to nginx",
  "Apache2 Default Page", hoặc nội dung sau khi bỏ thẻ HTML ngắn hơn 120 ký tự.

Bật `verify=False` có chủ đích: rất nhiều website doanh nghiệp Việt Nam dùng chứng
chỉ hết hạn nhưng vẫn đang vận hành — coi chúng là "chết" sẽ sai.

## Xác minh lại khi nghi ngờ

Sale gọi không được → bấm **Kiểm tra lại** trên giao diện
(`POST /places/{id}/reverify`) → địa điểm quay về hàng đợi, worker quét lại và cập
nhật trạng thái mới nhất từ Google. Cột `last_verified_at` cho biết dữ liệu tươi
tới đâu.

Ngoài ra `ttl_days` (mặc định 90) khiến mọi lần chạy job sau đó tự động quét lại
những địa điểm đã quá hạn — danh sách tự làm mới theo thời gian mà không tốn công
ai cả.

## Ví dụ thật (đo được khi chạy thử)

```
Trái cây 350       ACTIVE 75  [WEBSITE_DEAD]        website traicay350.com không truy cập được
Tiệm trà Khánh Thương ACTIVE 71 [NO_WEBSITE, FEW_REVIEWS, REVIEWS_STALE_2Y]
365HappyFlower     ACTIVE 100 []                     web sống, đánh giá mới 1 ngày trước
```

Lưu ý cách đọc: điểm số là **thứ tự ưu tiên gọi**, không phải phán quyết. Lý do
luôn hiển thị kèm để người dùng tự quyết — hệ thống không giấu cơ sở suy luận.
