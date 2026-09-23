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
  keyword_map?: Record<string, string[]>   // từ khoá riêng theo mã quốc gia, vd {"TH": ["fruit wholesaler"]}
  enrich_website?: boolean           // mặc định true — kiểm tra website sống/chết
  ttl_days?: number                  // mặc định 90 — bỏ qua địa điểm đã quét gần đây
  // mặc định true — bỏ qua luôn cả TRUY VẤN đã chạy xong trong `ttl_days` ngày.
  // `ttl_days` một mình chỉ tiết kiệm ở pha chi tiết; pha tìm kiếm vẫn cuộn lại
  // toàn bộ danh sách (1-3 phút/truy vấn) để rồi thấy mọi địa điểm đều đã có.
  skip_recent_queries?: boolean
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
  // Vì sao vòng cuộn danh sách dừng lại. `results_found` một mình KHÔNG cho biết
  // địa bàn đã quét hết hay chưa — 95 kết quả kèm "exhausted" là xong, 95 kết quả
  // kèm "cut_off" là còn sót. null khi truy vấn chưa chạy xong.
  //   exhausted  Google báo hết danh sách -> đã lấy trọn địa bàn
  //   cut_off    Google ngừng trả thêm dù còn -> phải chia nhỏ địa bàn
  //   cap        chạm trần max_results_per_query của chính người dùng
  //   empty      không có kết quả nào
  //   unknown    danh sách không hiện ra, không kết luận được
  //   recent     bỏ qua vì chính truy vấn này vừa chạy xong (status = "skipped")
  stop_reason: "exhausted" | "cut_off" | "cap" | "empty" | "unknown" | "recent" | null
}

type JobDetail = Job & { queries: JobQuery[] }

/*
 * Một ĐỊA BÀN CÒN SÓT. Không phải một dòng job_queries: đây là kết quả gộp mọi
 * lần chạy của CÙNG MỘT chuỗi truy vấn trên KHẮP các job, rồi chỉ giữ lần quét
 * gần nhất. Quét 34 tỉnh xong, câu hỏi là "tỉnh nào còn sót" chứ không phải
 * "job số 12 còn sót gì" — nên `job_id`/`job_name` ở đây là job của lần gần
 * nhất, dùng để mở ngược về đúng chỗ đã sinh ra con số này.
 */
type RemainingArea = {
  query: string
  stop_reason: "cut_off" | "cap" | "unknown"
  results_found: number | null
  finished_at: string | null
  job_id: number
  job_name: string
}

/*
 * Sinh MỘT job mới từ các dòng "Địa bàn còn sót" đang chọn.
 * Chỉ gửi chuỗi truy vấn — server tự tra lại lý do dừng của lần quét gần nhất
 * rồi quyết định việc phải làm. Trang có thể đã mở từ sáng, trong khi một job
 * khác vừa quét lại xong chính địa bàn đó.
 */
type RemainingSplitRequest = {
  queries: string[]                // >= 1, tối đa 500
  name?: string | null             // bỏ trống -> "Chia nhỏ địa bàn còn sót 23/09 14:05"
  max_results_per_query?: number   // mặc định 200 — trần mới cho các dòng "cap"
}

type SplitAction =
  | "subdivide"   // cut_off -> bung địa bàn xuống cấp dưới
  | "raise_cap"   // cap     -> chạy lại y nguyên, chỉ khác trần
  | "retry"       // unknown -> chạy lại y nguyên
  | "skip"        // không còn việc phải làm

type SplitPlanItem = {
  query: string
  stop_reason: "cut_off" | "cap" | "unknown" | null
  hanh_dong: SplitAction
  tu_khoa: string | null
  dia_diem: string | null
  cap: "country" | "province" | "ward" | null   // cấp của địa bàn hiện tại
  so_truy_van: number          // dòng này sinh ra bao nhiêu truy vấn
  ly_do: string | null         // vì sao 0 — hiện thẳng cho người dùng
}

type SplitPlan = {
  items: SplitPlanItem[]
  total_queries: number
  skipped: number              // số dòng không sinh ra truy vấn nào
  estimated_minutes: number    // ~40 giây/truy vấn, làm tròn lên
}
```

| Method | Path | Body / Query | Data |
|---|---|---|---|
| POST | `/jobs` | `JobCreate` | `Job` |
| GET | `/jobs` | `page, size, status?` | `Page<Job>` |
| GET | `/jobs/remaining-areas` | `page, size, stop_reason?` | `Page<RemainingArea>` |
| POST | `/jobs/remaining-areas/split-preview` | `RemainingSplitRequest` | `SplitPlan` |
| POST | `/jobs/remaining-areas/split` | `RemainingSplitRequest` | `Job` |
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

### GET /jobs/remaining-areas

Những địa bàn Google chưa trả hết mà người dùng còn phải xử — nguồn dữ liệu cho trang
"Địa bàn còn sót". Trả về `Page<RemainingArea>`, phân trang ở server như mọi danh sách khác.

- `stop_reason` (tuỳ chọn) chỉ nhận `cut_off` | `cap` | `unknown`; bỏ trống là cả ba.
  Giá trị khác trả `VALIDATION_ERROR` (422) — im lặng trả cả ba khi người dùng gõ nhầm
  `?stop_reason=cutoff` sẽ khiến họ tin mình đang nhìn đúng một nhóm.
- Mỗi chuỗi truy vấn chỉ ra MỘT dòng: **lần quét gần nhất** của nó. Chạy lại mà ra
  `exhausted` thì nó biến mất khỏi danh sách, dù mười lần trước đều `cut_off`.
- "Lần quét" chỉ tính những dòng có `stop_reason` thuộc
  `exhausted|cut_off|cap|empty|unknown`. Hai thứ bị loại dù `finished_at` mới hơn:
  lượt `recent` (bỏ qua, không hề chạm tới Google — mà cơ chế bỏ qua lại coi `cut_off`
  là "chạy lại cũng ra y hệt", nên đúng những tỉnh còn sót mới hay bị bỏ qua) và
  `stop_reason = null` (truy vấn lỗi hoặc chưa chạy). Tính hai thứ đó là lần gần nhất
  thì địa bàn còn sót sẽ lặng lẽ rơi khỏi danh sách đúng lúc nó vẫn còn sót.
- Sắp xếp: lần quét mới nhất lên đầu.

### POST /jobs/remaining-areas/split-preview · POST /jobs/remaining-areas/split

Bước còn thiếu của quy trình: trang "Địa bàn còn sót" cho NHÌN thấy tỉnh nào bị Google
cắt, hai endpoint này biến các dòng đang chọn thành **một** job mới. Cùng một bản kế
hoạch, khác đúng một điểm — `split-preview` không ghi gì, `split` thì tạo job.

Việc phải làm với mỗi dòng do server tra lại từ lần quét gần nhất, không lấy theo
`stop_reason` mà giao diện đang hiển thị:

| Lý do dừng | `hanh_dong` | Làm gì |
|---|---|---|
| `cut_off` | `subdivide` | Tách chuỗi thành `<từ khoá> <địa điểm>`, bung địa điểm xuống cấp dưới (quốc gia → tỉnh, tỉnh → phường/xã), rồi ghép lại với từ khoá cũ |
| `cap` | `raise_cap` | Chạy lại NGUYÊN VĂN. Địa bàn không có lỗi gì — chỉ cần `max_results_per_query` cao hơn lần trước |
| `unknown` | `retry` | Chạy lại NGUYÊN VĂN |
| khác / chưa từng chạy | `skip` | Không sinh dòng nào, kèm `ly_do` |

**Bung được tới đâu** — dữ liệu cấp phường/xã **chỉ Việt Nam mới có** (§5). Nên:

- Tỉnh Việt Nam → ra phường/xã (TP.HCM: 168 truy vấn cho mỗi từ khoá).
- Tỉnh nước ngoài → **không sinh dòng nào**, `ly_do` nói rõ. Đây là chỗ dễ hỏng ngầm
  nhất: `/geo/expand` với `ward:"ALL"` cho một tỉnh không có phường/xã KHÔNG báo lỗi,
  nó trả về đúng một dòng — chính tỉnh cũ — nên nếu không chặn thì người dùng nhận một
  job y hệt cái vừa chạy.
- Đã ở cấp phường/xã, hoặc chuỗi tự gõ tay mà danh mục không nhận ra (danh mục dùng tên
  chuẩn: `Krung Thep Maha Nakhon`, không phải `Bangkok`) → cũng không sinh dòng nào,
  kèm `ly_do`.

Job tạo ra luôn đặt **`skip_recent_queries = false`**. Không phải tuỳ chọn: cơ chế bỏ
qua coi `cut_off` là "chạy lại cũng ra y hệt", nên để mặc định `true` thì chính những
truy vấn vừa sinh ra ở đây bị bỏ qua sạch — job báo hoàn tất sau vài giây mà không quét
gì. `params.keywords` / `params.locations` ghi lại những gì đã dùng; danh sách truy vấn
THẬT nằm ở `job_queries`.

Các tuỳ chọn quét còn lại (`detail_mode`, `enrich_website`, `ttl_days`, `region`) lấy
GIÁ TRỊ MẶC ĐỊNH, không kế thừa job cũ: các dòng được chọn có thể đến từ nhiều job khác
nhau nên không có "job cũ" nào để kế thừa. `hl`/`gl` thì ngược lại — tính theo quốc gia
của từng truy vấn như mọi job khác, và giữ nguyên giá trị lần chạy trước khi không nhận
ra được địa bàn (đổi `gl` là đổi luôn địa bàn Google tìm, "chạy lại y nguyên" sẽ không
còn y nguyên).

Truy vấn trùng nhau chỉ sinh MỘT lần (chọn cả một tỉnh `cut_off` lẫn một phường của nó);
dòng bị nuốt vẫn có mặt trong `items` với `so_truy_van: 0` và `ly_do` giải thích.

Không sinh được truy vấn nào → `split` trả `VALIDATION_ERROR` (422) chứ không tạo job rỗng.

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
  // ĐỊA CHỈ ĐẦY ĐỦ, hoặc null. Chỉ lấy từ trang chi tiết nên luôn có đủ
  // đường/phường/tỉnh/quốc gia. KHÔNG bao giờ là một mẩu.
  address: string | null
  // Mẩu địa chỉ trên thẻ kết quả ("Phan Huy Ích"), chỉ có nghĩa khi `address`
  // còn null. Giao diện PHẢI hiện kèm dấu hiệu chưa đầy đủ. Đo thật trên 2.526
  // dòng chưa mở chi tiết: 39% không có cả mẩu này, phần còn lại dài TB 20 ký tự.
  address_short: string | null
  country_code: string | null       // ISO alpha-2, vd "TH"; null với dữ liệu quét trước V0003
  country_name: string | null       // tên tiếng Việt tra sẵn ở backend, vd "Thái Lan"
  // NGUỒN đã xác định ra quốc gia — đây là một PHỎNG ĐOÁN, và nguồn cho biết
  // phỏng đoán đó mạnh tới đâu. Quan trọng vì quốc gia quyết định vùng đọc số
  // điện thoại NỘI ĐỊA, mà số đọc sai vùng vẫn "hợp lệ" nên không gì báo:
  //   address  đọc từ tên nước ở đuôi địa chỉ — chắc chắn nhất
  //   coords   toạ độ nằm trong biên giới nước nào — sai số ~1-2km ở sát biên
  //   gl       đoán theo nước đang tìm — YẾU NHẤT, nên kiểm lại
  country_source: "address" | "coords" | "gl" | null
  // Quốc gia mà SỐ ĐIỆN THOẠI thuộc về. Lệch `country_code` KHÔNG phải lỗi —
  // doanh nghiệp Thái niêm yết số Việt Nam thường là đầu mối có người Việt.
  phone_country_code: string | null
  // Dạng QUỐC TẾ theo quy ước nhóm số của từng nước (libphonenumber):
  //   "+84 901 234 567"  "+66 2 281 9715"  "+81 3-1234-5678"  "+1 212-555-1234"
  // Cố ý không dùng dạng nội địa: nó bỏ mã nước đi nên số Bangkok ra
  // "02 281 9715", người dùng Việt Nam đọc thành số trong nước rồi gọi không được.
  phone: string | null
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
  // Trạng thái CHĂM SÓC của bên mình — khác hẳn `status` (worker đã quét chưa)
  // và `liveness_label` (doanh nghiệp còn sống không). Một lead có thể mang cả ba.
  contact_status: "new" | "called" | "interested" | "rejected"
  contact_note: string | null
  contact_at: string | null   // chỉ đổi khi TRẠNG THÁI đổi, không đổi khi sửa ghi chú
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
| PATCH | `/places/{id}/contact` | `{ status, note? }` — `note` null = giữ nguyên, "" = xoá | `Place` |
| GET | `/places/countries` | — | `[{ code, name, count }]` — chỉ những nước CÓ dữ liệu, count giảm dần |
| GET | `/places/events` | `token` | SSE — xem bên dưới |
| GET | `/places/export` | như `/places` + `format` + `token` | file tải về |

`GET /places/events` giữ một kết nối và **chỉ bắn tin khi dữ liệu thật sự đổi**:

```
event: places
data: { "max_id": 9876, "last_change": "2026-09-23T04:56:25Z" }
```

Gói tin CỐ Ý không chứa `total` lẫn dòng dữ liệu nào. Bỏ `total` vì `count(*)`
ép Postgres quét toàn bảng (70 ms ở 300k dòng) cho một con số giao diện không
đọc tới — nó chỉ dùng gói tin làm tín hiệu "có thay đổi" rồi tự nạp lại danh sách.

Gói tin cũng không chứa dòng dữ liệu nào — bảng đang lọc/sắp/phân trang nên chỉ
server mới biết trang hiện tại gồm những dòng nào. Giao diện nhận tin rồi tự nạp
lại danh sách. Nhịp bám theo worker: 2 giây khi đang quét, 10 giây khi rảnh (chỉ
worker mới ghi vào bảng places, worker rảnh thì dữ liệu không thể đổi).

Token đi ở query param vì `EventSource` không gắn được header — giống `/jobs/{id}/events`.

Query lọc dùng chung cho `/places` và `/places/export`:

```
q                 tìm trong tên + địa chỉ (không dấu cũng khớp)
job_id            chỉ địa điểm của job này
keyword           đúng một LƯỢT TÌM đã ra nó — cả chuỗi "<từ khoá> <địa điểm>"
country           mã ISO alpha-2 (TH, VN...), không phân biệt hoa thường
contact_status    new | called | interested | rejected
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

## 6. Từ khoá bản địa theo quốc gia

Từ khoá tiếng Việt gần như vô dụng ngoài Việt Nam. Đo thực tế:

| Cách viết | Kết quả |
|---|---|
| `xuất nhập khẩu trái cây Bangkok` + `gl=vn` | **2** — và cả hai đều ở TP.HCM, sai địa bàn |
| `fruit wholesaler Bangkok` + `gl=th` | **60+**, 52 có SĐT |
| `ผู้ค้าส่งผลไม้ กรุงเทพ` + `gl=th` | **60+**, 42 có SĐT |

Hai cơ chế xử lý:

1. **`hl`/`gl` theo từng truy vấn** — backend suy ra quốc gia từ đuôi chuỗi địa điểm
   rồi gắn ngôn ngữ/quốc gia riêng cho truy vấn đó. FE không cần làm gì.
   `hl` cố ý chỉ nhận `vi` (Việt Nam) hoặc `en` (mọi nơi khác), vì bộ bóc tách chỉ
   đọc được hai ngôn ngữ này — đặt `hl=th` thì mọi tín hiệu chấm sống/chết thành rỗng.
2. **`keyword_map`** — từ khoá riêng cho mỗi quốc gia, do người dùng duyệt trước khi chạy.

```ts
type CountryKeywords = {
  country_code: string
  country_name: string
  language: string
  keywords: string[]
  source: "ai" | "cache" | "user" | "original" | "fallback"
}
```

| Method | Path | Body | Data |
|---|---|---|---|
| GET | `/keywords/status` | — | `{ ai_available: boolean }` |
| POST | `/keywords/plan` | `{ keywords, locations?, countries? }` | `{ total, home, cached, need, limit, over_limit, ai_available }` |
| POST | `/keywords/localize` | `{ keywords, locations?, countries? }` | `{ items: CountryKeywords[], ai_available, warning }` |
| POST | `/keywords/save` | `{ keywords, country_code, language, translated }` | `{ saved: true }` |

`/keywords/plan` và `/keywords/localize` suy ra danh sách quốc gia từ `locations`
(đuôi mỗi dòng), hoặc nhận thẳng `countries`.

`/keywords/plan` **không gọi AI** — chỉ đếm quốc gia và tra bộ nhớ đệm, nên giao
diện gọi được mỗi khi người dùng sửa từ khoá/địa điểm. Nó tồn tại để chặn trần
`BCD_AI_MAX_COUNTRIES` (mặc định 40) TRƯỚC khi bấm nút, thay vì báo sau khi đã
chờ. Trần tính trên `need` chứ không phải `total`: 60 nước mà 50 nước đã có bản
dịch lưu sẵn thì chỉ còn 10 nước cần gọi AI.

Ý nghĩa `source` của `/keywords/localize`:

| Giá trị | Nghĩa |
|---|---|
| `original` | Việt Nam — dùng thẳng từ khoá gốc, không dịch |
| `ai` | Vừa sinh bằng AI |
| `cache` | Lấy từ bộ nhớ đệm của lần trước |
| `user` | Bản người dùng đã sửa tay, AI không ghi đè |
| `fallback` | Không dịch được → tạm dùng từ khoá gốc, xem `warning` |

**Endpoint này không bao giờ trả lỗi vì AI.** Chưa cấu hình khoá, sai khoá hay quá hạn
mức đều trả về từ khoá gốc kèm `warning`; người dùng vẫn tạo job được như thường.
