# bot_craw_data — backend

Công cụ nội bộ quét Google Maps theo từ khoá để lấy danh sách doanh nghiệp:
**tên công ty · vị trí · số điện thoại · website**, kèm **đánh giá công ty còn
hoạt động hay đã ngừng**.

Thiết kế cho đúng một hoàn cảnh: chạy **1 luồng** trên **một máy đặt tại văn phòng**,
đi internet bằng **IP của công ty**, **không dùng proxy**. Mọi lựa chọn kỹ thuật
trong repo này đều bám hoàn cảnh đó.

Frontend nằm ở repo riêng: [`bot_craw_data_fe`](https://github.com/MaiAnhKhoi/bot_craw_data_fe).

## Chạy nhanh

Clone hai repo **nằm cạnh nhau**, rồi chạy một lệnh duy nhất trong repo backend:

```bash
git clone https://github.com/MaiAnhKhoi/bot_craw_data_be.git ago_bot_craw_data
git clone https://github.com/MaiAnhKhoi/bot_craw_data_fe.git bot_craw_data_fe
```

```bash
cd ago_bot_craw_data && cp .env.example .env
```

Sửa `BCD_JWT_SECRET_KEY` và `BCD_ADMIN_PASSWORD` trong `.env`, rồi:

```bash
docker compose -f docker-compose.yml -f docker-compose.web.yml up -d --build
```

Xong là có đủ 6 dịch vụ:

| Dịch vụ | Địa chỉ | Vai trò |
|---|---|---|
| `web` | **http://localhost:3000** | Giao diện — mọi người dùng vào đây |
| `api` | http://localhost:8000/docs | REST API + tài liệu tương tác |
| `worker` | — | Cào Google Maps, **chỉ được chạy đúng một bản** |
| `db` | localhost:5432 | PostgreSQL |
| `migrate` | — | Chạy `alembic upgrade head` một lần rồi thoát |
| `backup` | thư mục `backups/` | `pg_dump` mỗi ngày, tự dọn bản cũ |

Tài khoản quản trị được tạo tự động ở lần khởi động đầu từ `BCD_ADMIN_USERNAME` /
`BCD_ADMIN_PASSWORD`.

Ngại gõ dài thì bỏ chú thích dòng `COMPOSE_FILE=...` trong `.env`, sau đó chỉ cần
`docker compose up -d --build`. Chỉ muốn backend (không có giao diện) thì dùng
`docker compose up -d --build` khi chưa bật dòng đó.

Trình duyệt chỉ nói chuyện với `web`; `web` mới gọi sang `api` trong mạng nội bộ của
Docker. Nhờ vậy mọi request là same-origin nên không bao giờ dính CORS.

Chạy tay khi phát triển:

```bash
python -m venv .venv && .venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m uvicorn app.main:app --reload      # API
.venv\Scripts\python -m app.workers.runner                 # worker (cửa sổ khác)
```

## Kiến trúc

```
Người dùng ─ Next.js ─HTTP─> FastAPI ──> PostgreSQL <── Worker (1 Chromium, 1 context)
                                 ▲                              │
                                 └── SSE tiến độ                └──> Google Maps
                                                                └──> httpx kiểm tra website
```

Không có Redis, không có message broker: **hàng đợi nằm trong Postgres**
(`SELECT ... FOR UPDATE SKIP LOCKED`). Một luồng thì không có gì để điều phối, và
bỏ bớt một dịch vụ là bỏ bớt một chỗ để hỏng. Muốn thêm worker thứ hai ở máy khác
(IP khác) thì chạy thêm tiến trình, không phải sửa kiến trúc.

```
app/
  core/                config · database · response · pagination · security · deps · exceptions · logging
  modules/
    identity/          đăng nhập
    scraper/
      engine/          browser · search · detail · parsers · normalize · liveness · website · pacing
      job/             tạo/điều khiển job quét            (router · service · repository · entity)
      place/           danh sách, lọc, xuất file, ghi dữ liệu
      status/          nhịp tim của worker
    stats/             số liệu tổng quan
  workers/runner.py    vòng lặp worker — 1 luồng
migrations/            Alembic là nguồn chuẩn của schema
scripts/               tiện ích chạy tay + backup_loop.sh (vòng lặp sao lưu DB)
docs/                  API_CONTRACT · ANTI_BLOCK · LIVENESS · BACKUP
```

Luật giống `eco_backend`: transaction nằm ở tầng **service** (router không commit,
repository không commit); cấm `Base.metadata.create_all()`; mọi API trả cùng phong
bì `{success, data, error, request_id}`.

## Hai pha quét và vì sao nó nhanh

| Pha | Việc | Chi phí |
|---|---|---|
| 1. Tìm kiếm | Cuộn hết danh sách, **bóc luôn nội dung thẻ**: tên, SĐT, danh mục, địa chỉ rút gọn, rating, trạng thái mở cửa | ~10–20 giây cho 70–100 địa điểm |
| 2. Chi tiết | Chỉ mở trang chi tiết khi còn thiếu trường cần — **website chỉ có ở đây** | ~2 giây/trang |
| 3. Làm giàu | Kiểm tra website còn sống bằng httpx, 8 kết nối song song | không đụng Google |

Điểm mấu chốt về hiệu năng: mỗi địa điểm chỉ tốn **một lần `page.evaluate`**. Bóc 4
trường hay 15 trường chênh nhau vài mili giây trên một trang mất 1–2 giây, nên ta
bóc đủ tín hiệu để chấm sống/chết — nhưng **xuất ra Excel đúng 4 cột nghiệp vụ** +
cột tình trạng.

`detail_mode` của mỗi job:

| Giá trị | Kết quả | Tốc độ |
|---|---|---|
| `never` | Không có website, địa chỉ rút gọn | ~100 địa điểm / 20 giây |
| `missing_only` *(mặc định)* | Đủ 4 trường | ~8 địa điểm / phút |
| `always` | Luôn làm mới toàn bộ | chậm nhất |

## Chống chặn

Không có proxy thì tốc độ là lớp phòng thủ chính: nghỉ 4–8 giây/trang, nhịp tự
điều chỉnh khi có dấu hiệu bị soi, cầu dao nghỉ 1 giờ khi gặp trang chặn, nghỉ đêm
2h–6h, hồ sơ trình duyệt bền, chặn ảnh/font, cuộn từng nấc. Chi tiết và ngưỡng
cảnh báo: [`docs/ANTI_BLOCK.md`](docs/ANTI_BLOCK.md).

## Phát hiện công ty đã ngừng hoạt động

Điểm 0–100 kèm lý do, ghép từ: cờ đóng cửa của Google · tuổi đánh giá mới nhất ·
số lượng đánh giá · website còn sống không · có SĐT hợp lệ không · có công bố giờ
mở cửa không. Chi tiết công thức: [`docs/LIVENESS.md`](docs/LIVENESS.md).

Nhãn `ACTIVE` / `SUSPECT` / `DEAD` dùng để **xếp thứ tự ưu tiên gọi**, và lý do
luôn hiển thị kèm để người dùng tự quyết.

## API

Hợp đồng đầy đủ: [`docs/API_CONTRACT.md`](docs/API_CONTRACT.md). Tài liệu tương
tác: `/docs`. Sinh lại file OpenAPI cho frontend (không cần DB):

```bash
python -m scripts.export_openapi ../bot_craw_data_fe/openapi/api-docs.json
```

## Cấu hình

Toàn bộ biến môi trường có tiền tố `BCD_`, xem `.env.example`. Đáng chú ý:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `BCD_PACE_MIN_SECONDS` / `MAX` | 4 / 8 | Nghỉ giữa hai trang — **lớp chống chặn chính** |
| `BCD_NIGHT_REST_START` / `END` | 2 / 6 | Khung giờ nghỉ đêm (đặt bằng nhau để tắt) |
| `BCD_BLOCK_BACKOFF_SECONDS` | 3600 | Nghỉ bao lâu khi gặp trang chặn |
| `BCD_BROWSER_ENGINE` | `playwright` | Đổi `patchright` để che dấu vết sâu hơn |
| `BCD_HEADLESS` | `true` | Đặt `false` khi cần nhìn trình duyệt lúc gỡ lỗi |
| `BCD_EXPORT_MAX_ROWS` | 100000 | Trần số dòng cho một lần xuất file |
| `BCD_BACKUP_EVERY_HOURS` / `KEEP` | 24 / 14 | Tuổi tối đa của bản sao lưu, và số bản giữ lại |

## Kiểm thử

```bash
pytest -q                      # test offline: parser, liveness, chuẩn hoá, bóc DOM trên fixture
GMAPS_LIVE=1 pytest -m live    # chạm Google thật — chạy hằng tuần để phát hiện đổi DOM
```

Nếu `stats/overview` cho thấy độ đầy đủ cột SĐT rơi đột ngột giữa hai lần chạy thì
Google đã đổi DOM: chạy test `live`, sửa selector trong `engine/detail.py` /
`engine/search.py` (mọi selector tập trung ở hai file đó).

## Vận hành

- Máy: 8 GB RAM trở lên, để 24/7. Chiếm dụng thực tế ~1,7 GB (Chromium 0,8 + Postgres 0,4 + API 0,25 + web 0,2).
- Tự bật lại: BIOS "Restore on AC Power Loss", tắt sleep, `restart: unless-stopped` trong compose.
- Truy cập từ ngoài văn phòng: Cloudflare Tunnel (miễn phí, không cần IP tĩnh, không mở port).

### Sao lưu

Service `backup` chạy sẵn cùng stack, đổ `pg_dump -Fc` vào thư mục **`backups/`** và
giữ 14 bản gần nhất. Lịch **không phải cron**: máy văn phòng tối bị tắt, nên nó hỏi
"bản gần nhất bao nhiêu tuổi" mỗi 30 phút và chạy bù ngay khi quá 24 giờ — bật máy
lúc 9h sáng là 9h có bản sao lưu, không mất trắng một ngày.

```bash
docker compose logs -f backup     # xem nó có còn chạy không
```

File dump nằm **cùng ổ cứng với database**, nên nó chống được xoá nhầm chứ không
chống được hỏng ổ: mỗi tuần vẫn phải tự copy file mới nhất sang Drive hoặc ổ ngoài.
Lệnh khôi phục đầy đủ và cách thử một bản dump trước khi tin nó:
[`docs/BACKUP.md`](docs/BACKUP.md).

## Lưu ý pháp lý

Dữ liệu là thông tin doanh nghiệp công khai, nhưng tự động thu thập là **trái Điều
khoản dịch vụ của Google** — rủi ro là bị chặn IP tạm thời. Với số điện thoại của
hộ kinh doanh cá thể, tuân thủ Nghị định 13/2023 về bảo vệ dữ liệu cá nhân khi lưu
trữ và dùng cho mục đích tiếp thị.
