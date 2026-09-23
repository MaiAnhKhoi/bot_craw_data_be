# API Contract — bot_craw_data

Hợp đồng giữa `bot_craw_data_be` (FastAPI) và `bot_craw_data_fe` (Next.js).
**Đây là nguồn chuẩn duy nhất.** Đổi contract = sửa file này trước, sửa code sau.

- Base URL: `http://localhost:8000/api/v1` (FE gọi qua biến `NEXT_PUBLIC_API_BASE_URL`)
- Mọi response bọc trong envelope; FE bóc `data` ở tầng axios interceptor.
- Auth: `Authorization: Bearer <access_token>` cho mọi endpoint trừ `/auth/login` và `/health`.

## Envelope

```jsonc
// thành công
{ "success": true,  "data": { }, "error": null, "request_id": "a1b2c3" }
// lỗi
{ "success": false, "data": null, "error": { "code": "PLACE_NOT_FOUND", "message": "..." }, "request_id": "a1b2c3" }
```

Mã lỗi dùng chung: `UNAUTHORIZED` · `FORBIDDEN` · `VALIDATION_ERROR` · `NOT_FOUND` ·
`JOB_INVALID_STATE` · `EXPORT_TOO_LARGE` · `INTERNAL_ERROR`.

## Phân trang

Query params: `page` (1-based, mặc định 1), `size` (mặc định 50, tối đa 200).

```jsonc
{ "items": [ ], "page": 1, "size": 50, "total": 1234, "pages": 25 }
```

## 1. Auth

| Method | Path | Body | Data trả về |
|---|---|---|---|
| POST | `/auth/login` | `{ username, password }` | `{ access_token, token_type:"bearer", expires_in, user }` |
| GET | `/auth/me` | — | `User` |

```ts
type User = { id: number; username: string; full_name: string | null; is_active: boolean }
```

## 2. Jobs

```ts
type JobStatus = "queued" | "running" | "paused" | "done" | "failed" | "cancelled"
type JobPhase  = "search" | "detail" | "enrich" | "idle"

type JobCreate = {
  name: string
  keywords: string[]                 // bắt buộc, >= 1
  locations?: string[]               // nhân tổ hợp với keywords
  hl?: string                        // "vi" (mặc định)
  gl?: string                        // "vn"
  region?: string                    // "VN" — dùng chuẩn hoá SĐT
  max_results_per_query?: number     // mặc định 200
  detail_mode?: "always" | "missing_only" | "never"   // mặc định "missing_only"
  enrich_website?: boolean           // mặc định true — kiểm tra website sống/chết
  ttl_days?: number                  // mặc định 90 — bỏ qua địa điểm đã quét gần đây
}

type Job = {
  id: number
  name: string
  status: JobStatus
  phase: JobPhase
  params: JobCreate
  total_queries: number;  done_queries: number
  total_places: number;   done_places: number;  failed_places: number
  new_places: number
  blocked_count: number
  rate_per_min: number | null
  started_at: string | null;  finished_at: string | null
  last_error: string | null
  created_at: string;  updated_at: string
}

type JobQuery = {
  id: number; query: string
  status: "pending" | "running" | "done" | "failed" | "skipped"
  results_found: number | null; error: string | null
}

type JobDetail = Job & { queries: JobQuery[] }
```

| Method | Path | Body / Query | Data |
|---|---|---|---|
| POST | `/jobs` | `JobCreate` | `Job` |
| GET | `/jobs` | `page, size, status?` | `Page<Job>` |
| GET | `/jobs/{id}` | — | `JobDetail` |
| POST | `/jobs/{id}/pause` | — | `Job` |
| POST | `/jobs/{id}/resume` | — | `Job` |
| POST | `/jobs/{id}/cancel` | — | `Job` |
| GET | `/jobs/{id}/events` | `token` (query) | **SSE** |

SSE `GET /jobs/{id}/events?token=<access_token>` — `Content-Type: text/event-stream`, gửi mỗi 2 giây.
`EventSource` của trình duyệt không gắn được header nên endpoint này nhận token qua query param
(chỉ chấp nhận trên cùng origin, đi qua rewrite của Next.js).

```
event: progress
data: {"id":12,"status":"running","phase":"detail","total_queries":24,"done_queries":10,
       "total_places":840,"done_places":315,"failed_places":4,"new_places":290,
       "blocked_count":0,"rate_per_min":7.8,"updated_at":"2026-09-23T03:11:02Z"}

event: done
data: {"id":12,"status":"done"}
```

Chuyển trạng thái hợp lệ: `queued→running→(paused↔running)→done|failed|cancelled`.
Gọi sai trạng thái trả `JOB_INVALID_STATE` (409).

## 3. Places

**4 trường bắt buộc theo yêu cầu**: `name` (tên công ty), `address` (vị trí),
`phone` (số điện thoại), `website`. Các trường còn lại phục vụ lọc & chấm sống/chết.

```ts
type LivenessLabel = "ACTIVE" | "SUSPECT" | "DEAD"
type BusinessStatus = "OPERATIONAL" | "CLOSED_TEMPORARILY" | "CLOSED_PERMANENTLY"
type WebsiteStatus = "OK" | "DEAD" | "PARKED" | "UNCHECKED" | "NONE"

type Place = {
  id: number
  name: string
  address: string | null            // vị trí (địa chỉ đầy đủ nếu đã mở trang chi tiết)
  phone: string | null              // định dạng quốc gia, vd "090 123 4567"
  phone_e164: string | null         // "+84901234567"
  phone_valid: boolean | null
  website: string | null
  website_status: WebsiteStatus
  category: string | null
  rating: number | null
  review_count: number | null
  business_status: BusinessStatus
  liveness_score: number            // 0..100
  liveness_label: LivenessLabel
  liveness_reasons: string[]        // mã lý do, FE tự dịch sang tiếng Việt
  latest_review_days: number | null // tuổi đánh giá mới nhất (ngày)
  lat: number | null; lng: number | null
  maps_url: string | null
  keywords: string[]
  detail_scraped: boolean
  scraped_at: string | null
  last_verified_at: string | null
}
```

Mã `liveness_reasons` (FE ánh xạ sang câu tiếng Việt):
`CLOSED_PERMANENTLY` · `CLOSED_TEMPORARILY` · `NO_PHONE` · `INVALID_PHONE` ·
`NO_WEBSITE` · `WEBSITE_DEAD` · `WEBSITE_PARKED` · `NO_REVIEWS` · `FEW_REVIEWS` ·
`REVIEWS_STALE_1Y` · `REVIEWS_STALE_2Y` · `REVIEWS_STALE_3Y` · `NO_HOURS`.

| Method | Path | Query | Data |
|---|---|---|---|
| GET | `/places` | xem bên dưới | `Page<Place>` |
| GET | `/places/{id}` | — | `Place` |
| POST | `/places/{id}/reverify` | — | `Place` (đặt lại `pending` để worker quét lại) |
| GET | `/places/export` | như `/places` + `format` + `token` | file tải về |

Query lọc dùng chung cho `/places` và `/places/export`:

```
q                 tìm trong tên + địa chỉ (không dấu cũng khớp)
job_id            chỉ địa điểm của job này
keyword           đúng một từ khoá đã tìm ra nó
liveness          ACTIVE | SUSPECT | DEAD   (lặp được: ?liveness=ACTIVE&liveness=SUSPECT)
business_status   OPERATIONAL | CLOSED_TEMPORARILY | CLOSED_PERMANENTLY
has_phone         true | false
has_website       true | false
min_rating        số
sort              liveness | name | rating | review_count | scraped_at   (thêm "-" để đảo: -rating)
format            xlsx | csv | json        (chỉ /places/export)
```

`/places/export` trả file trực tiếp (`Content-Disposition: attachment`). Giống endpoint SSE,
nó nhận thêm `token` qua query string vì thẻ `<a download>` của trình duyệt không gắn
được header `Authorization`.
Quá 100.000 dòng trả lỗi `EXPORT_TOO_LARGE`.

## 4. Stats

```ts
type Overview = {
  total: number
  done: number; pending: number; failed: number
  by_liveness: { ACTIVE: number; SUSPECT: number; DEAD: number }
  completeness: { phone: number; website: number; address: number }   // số dòng có dữ liệu
  today_new: number
  last_14_days: { date: string; count: number }[]
  top_keywords: { keyword: string; count: number }[]
}

type WorkerStatus = {
  alive: boolean
  last_heartbeat: string | null
  current_job_id: number | null
  current_phase: JobPhase
  pace_seconds: number          // khoảng nghỉ hiện tại giữa 2 trang
  blocked_today: number
  pages_last_hour: number
  night_rest: boolean           // đang trong khung giờ nghỉ đêm
}
```

| Method | Path | Data |
|---|---|---|
| GET | `/stats/overview` | `Overview` |
| GET | `/stats/worker` | `WorkerStatus` |
| GET | `/health` | `{ status: "ok", version, db: "ok" }` (không cần token) |

## 5. Địa giới hành chính (geo)

Danh mục tĩnh phục vụ ô chọn địa điểm theo cấp. Dữ liệu nằm trong file JSON đi kèm
backend nên không phụ thuộc dịch vụ ngoài lúc chạy.

**Phạm vi dữ liệu** — nói rõ để không kỳ vọng nhầm:

| Cấp | Phạm vi |
|---|---|
| Châu lục | 7 |
| Quốc gia | 249, đủ toàn thế giới, tên hiển thị tiếng Việt |
| Tỉnh/bang | ~3.500, đủ toàn thế giới (ISO 3166-2) |
| Phường/xã | **Chỉ Việt Nam** — 3.321 đơn vị theo địa giới từ 01/07/2025 |

```ts
type GeoItem = { code: string; name: string }
type GeoCountry = GeoItem & {
  name_en: string
  continent: string
  levels: 1 | 2 | 3    // 1 = chỉ quốc gia · 2 = có cấp tỉnh · 3 = có cả phường/xã
}
```

| Method | Path | Query | Data |
|---|---|---|---|
| GET | `/geo/continents` | — | `GeoItem[]` |
| GET | `/geo/countries` | `continent?`, `q?` | `GeoCountry[]` |
| GET | `/geo/provinces` | `country` (bắt buộc), `q?` | `GeoItem[]` |
| GET | `/geo/wards` | `province` (bắt buộc), `q?` | `GeoItem[]` |
| POST | `/geo/expand` | body bên dưới | `ExpandResponse` |

`q` tìm không dấu cũng khớp (`ho chi` ra `Thành phố Hồ Chí Minh`).

### POST /geo/expand

Biến lựa chọn theo cấp thành danh sách chuỗi địa điểm cụ thể để đổ vào ô `locations`
của job.

```ts
type ExpandRequest = {
  continent?: string | null   // mã, hoặc "ALL", hoặc bỏ trống
  country?: string | null
  province?: string | null    // bỏ trống = dừng ở cấp quốc gia
  ward?: string | null        // bỏ trống = dừng ở cấp tỉnh
}
type ExpandResponse = {
  locations: string[]   // "Phường Bến Thành, Thành phố Hồ Chí Minh, Việt Nam"
  total: number         // tổng THẬT, có thể lớn hơn số dòng trả về
  truncated: boolean    // true khi total vượt trần 5.000 dòng
}
```

Quy ước: `"ALL"` = **tách ra từng mục** ở cấp đó.

| Lựa chọn | Kết quả |
|---|---|
| `{country:"VN"}` | 1 dòng — `Việt Nam` |
| `{country:"VN", province:"ALL"}` | 34 dòng |
| `{country:"VN", province:"VN-79", ward:"ALL"}` | 168 dòng |
| `{country:"VN", province:"ALL", ward:"ALL"}` | 3.321 dòng |
| `{continent:"AS", country:"ALL"}` | 54 dòng |

**Châu lục chỉ là bộ lọc**, không bao giờ xuất hiện trong chuỗi địa điểm — truy vấn
`"vựa trái cây Châu Á"` là vô nghĩa với Google Maps.

Mã cụ thể không khớp thì trả rỗng chứ **không** âm thầm lùi về cấp trên. Riêng khi
chọn `"ALL"`, quốc gia/tỉnh thiếu dữ liệu cấp dưới sẽ được giữ lại ở cấp cao hơn —
chọn "phủ hết" mà mất nguyên một nước mới là sai.
