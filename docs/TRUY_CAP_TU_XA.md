# Cho sale vào từ mạng khác — Cloudflare Tunnel

Ba mức truy cập, chọn đúng mức mình cần rồi dừng lại ở đó:

| Ai cần vào | Cách | Phải làm gì |
|---|---|---|
| Chỉ trong văn phòng | Đã chạy sẵn | Ghim IP tĩnh trong LAN, mở cổng 3000 ở tường lửa |
| Thêm người từ nhà | Tunnel | Tài liệu này |
| Cả công ty, nhiều chi nhánh | Tunnel + Access | Tài liệu này, làm đủ cả Bước 5 |

## Vì sao không trỏ IP

Nhiều nhà mạng ở Việt Nam đặt thuê bao sau **CGNAT** — văn phòng không thực sự
sở hữu IP công cộng nào. Mở cổng trên router lúc đó **không có tác dụng gì cả**:
làm đúng hết vẫn không ai vào được, và cách duy nhất là gọi nhà mạng xin IP tĩnh
trả phí hàng tháng. Thêm nữa IP động đổi sau mỗi lần router khởi động lại, hôm
đó cả phòng mất truy cập.

Tunnel đi hướng ngược lại: máy chủ **tự mở kết nối đi ra** Cloudflare rồi giữ
đó. Không cổng nào mở vào, không cần IP, không đụng router.

**Tunnel KHÔNG đổi IP mà worker dùng để quét Google.** Nó chỉ ảnh hưởng chiều
vào. Rủi ro bị Google chặn không tăng.

## Cần có trước

- Một tên miền anh sở hữu, đã trỏ nameserver về Cloudflare.
- Tài khoản Cloudflare (gói miễn phí là đủ).

---

## Bước 1 — Tạo tunnel

1. Vào `one.dash.cloudflare.com` → **Networks** → **Tunnels** → **Create a tunnel**
2. Chọn **Cloudflared**, đặt tên (ví dụ `botcraw-vanphong`)
3. Tới màn "Install and run a connector", **copy đoạn token** — chuỗi rất dài bắt
   đầu bằng `eyJ...`

Đừng chạy lệnh cài mà Cloudflare gợi ý. Mình chạy bằng Docker, xem Bước 2.

## Bước 2 — Đặt token vào `.env`

Thêm một dòng vào `.env` của thư mục này:

```
CLOUDFLARE_TUNNEL_TOKEN=eyJhIjoiXXXX...
```

Token này là **bí mật ngang mật khẩu**: ai có nó đều dựng được một đường vào
mạng của anh. `.env` đã bị `.gitignore` chặn nên không lọt lên Git — đừng chép
nó sang chỗ khác.

## Bước 3 — Khai đường đi tới giao diện

Về lại trang tunnel, tab **Published application routes** → **Add a public
hostname**:

| Ô | Điền |
|---|---|
| Subdomain | `botcraw` (hoặc tên anh thích) |
| Domain | tên miền của anh |
| Type | `HTTP` |
| URL | `web:3000` |

`web:3000` là **tên service trong Docker Compose**, không phải `localhost:3000`.
`cloudflared` chạy trong cùng mạng Docker nên nó gọi thẳng sang container `web`
— đó cũng là lý do không cần mở cổng nào ra ngoài.

`HTTP` chứ không `HTTPS`: chặng từ Cloudflare tới máy anh đã nằm trong tunnel mã
hoá rồi, còn `web` bên trong chỉ nói HTTP. Chọn HTTPS ở đây là lỗi chứng chỉ.

## Bước 4 — Chạy

```bash
docker compose -f docker-compose.yml -f docker-compose.web.yml -f docker-compose.tunnel.yml up -d
```

Gõ dài thì đặt một dòng trong `.env`:

```
COMPOSE_FILE=docker-compose.yml:docker-compose.web.yml:docker-compose.tunnel.yml
```

Kiểm:

```bash
docker compose logs cloudflared --tail 20
```

Thấy `Registered tunnel connection` là xong. Mở
`https://botcraw.tenmiencuaanh.com` từ điện thoại dùng 4G (không dùng Wi-Fi văn
phòng) để chắc chắn nó đi qua Internet thật.

---

## Bước 5 — Cloudflare Access ⚠️ KHÔNG ĐƯỢC BỎ QUA

Hệ thống này **không giới hạn số lần đăng nhập sai**: không khoá tài khoản,
không chậm dần, không captcha. Trong mạng nội bộ thì chấp nhận được. Mở ra
Internet thì bot quét tìm thấy trong vài giờ và thử mật khẩu không giới hạn tốc
độ, mà dữ liệu bên trong là toàn bộ danh sách khách hàng tiềm năng của công ty.
Token lại sống 12 tiếng.

Access đặt một lớp đăng nhập **trước khi** request chạm tới ứng dụng. Miễn phí
tới 50 người.

1. `one.dash.cloudflare.com` → **Access** → **Applications** → **Add an
   application** → **Self-hosted**
2. Domain: đúng hostname vừa tạo ở Bước 3
3. **Add policy**:
   - Action: **Allow**
   - Include: **Emails ending in** → `@agoexim.com`
4. Identity provider: **One-time PIN** là đủ để bắt đầu — Cloudflare gửi mã qua
   email, không cần dựng gì thêm.

Xong thì ai mở link cũng phải nhập email công ty và mã trước, rồi mới thấy màn
đăng nhập của ứng dụng. Hai lớp, và lớp ngoài do Cloudflare chịu trách nhiệm.

---

## Gặp lỗi

| Hiện tượng | Nguyên nhân thường gặp |
|---|---|
| `502 Bad Gateway` | Điền `localhost:3000` thay vì `web:3000` ở Bước 3 |
| Lỗi chứng chỉ | Chọn `HTTPS` thay vì `HTTP` ở Bước 3 |
| Container `cloudflared` khởi động lại liên tục | Token sai hoặc thiếu trong `.env` |
| Vào được từ văn phòng, không vào được từ 4G | Đang ăn DNS nội bộ — thử ở mạng khác hẳn |

## Tắt truy cập từ xa

```bash
docker compose stop cloudflared
```

Giao diện vẫn chạy bình thường trong LAN. Các file compose khác không bị ảnh
hưởng — tunnel là một lớp phủ rời, gỡ ra lúc nào cũng được.
