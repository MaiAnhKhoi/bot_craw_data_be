# Sao lưu và khôi phục database

Toàn bộ kết quả cào nằm trong một database PostgreSQL duy nhất, trên một máy văn
phòng. Ổ cứng hỏng, Windows update hỏng, ai đó xoá nhầm — bất cứ cái nào cũng lấy
đi tất cả. Service `backup` trong `docker-compose.yml` dựng một bản `pg_dump` mỗi
ngày và tự dọn bản cũ.

Cài đặt: `scripts/backup_loop.sh` (chạy trong container `backup`).

## Lịch chạy: bù giờ, không phải cron

Đây là điểm khác biệt quan trọng nhất so với một cron job thông thường.

Máy này **bị tắt vào ban đêm**. Một lịch hẹn đúng 3h sáng thì hôm nào máy tắt là
hôm đó không có bản sao lưu nào — và không có gì báo cho ai biết. Cái giá thật
không phải là mất một bản dump, mà là suốt nhiều tháng cứ tin rằng mình đang được
bảo vệ, rồi phát hiện ra sự thật vào đúng ngày ổ cứng chết.

Nên container không hỏi *"đã tới giờ hẹn chưa?"* mà hỏi **"bản gần nhất bao nhiêu
tuổi?"**, mỗi 30 phút một lần:

| Tình huống | Điều xảy ra |
|---|---|
| Chưa có bản nào | Chạy ngay lúc container khởi động |
| Bản gần nhất > `BCD_BACKUP_EVERY_HOURS` (24h) | Chạy bù **ngay**, không đợi tới đêm |
| Bản gần nhất còn mới | Ghi một dòng log rồi ngủ tiếp 30 phút |

Hệ quả: tắt máy đi ngủ, sáng mai 9h bật lên thì **9h05 là có bản sao lưu của ngày
hôm đó**. Máy chạy 24/7 thì hành vi vẫn đúng như hẹn giờ mỗi 24 tiếng.

Tuổi được tính theo `mtime` của file chứ không theo tên file: bạn copy hay đổi tên
file bằng tay cũng không làm lệch lịch.

## File nằm ở đâu

Thư mục **`backups/`** ngay cạnh repo, trên chính ổ đĩa của máy — mở bằng
Explorer được, không cần lệnh Docker nào:

```
D:\project_ago_fruit\ago_bot_craw_data\backups\
    botcraw_20260923_141521.dump
    botcraw_20260922_090313.dump
    ...
```

Tên file là mốc thời gian **giờ địa phương** (`Asia/Ho_Chi_Minh`, theo
`BCD_TIMEZONE`). Định dạng là `pg_dump -Fc` — dạng custom, đã nén sẵn, chỉ
`pg_restore` đọc được; mở bằng Notepad sẽ thấy toàn ký tự rác, **đó là bình
thường**.

`backups/` bị `.gitignore` chặn. Một file `.dump` là bản sao đầy đủ của cả database
— dữ liệu khách hàng lẫn bảng `users` — nên không bao giờ được commit.

## ⚠️ Backup nằm cùng ổ cứng với database

Nói thẳng: cơ chế này **không chống được hỏng ổ cứng**. File dump nằm trên đúng cái
ổ đang chứa database. Ổ đó chết thì cả hai chết cùng nhau, và bạn không còn gì.

Nó chống được: xoá nhầm dữ liệu, migration hỏng, một bug ghi sai hàng loạt, cài lại
Windows, dựng lại Docker.

Muốn chống được hỏng ổ / mất máy / cháy văn phòng thì **phải có người copy file ra
chỗ khác**: Google Drive, OneDrive, hoặc một ổ cứng ngoài. Kéo thả từ Explorer là
xong. Cứ mỗi tuần một lần, lấy file mới nhất — đó là bước duy nhất mà phần mềm
không làm hộ được.

Quy tắc dễ nhớ: **dữ liệu chỉ thật sự an toàn khi tồn tại ở hai nơi khác nhau về
mặt vật lý.**

## Kiểm tra nó còn chạy

```bash
docker compose logs -f backup
```

Mỗi 30 phút phải có một dòng. Không thấy dòng nào mới trong hơn một tiếng nghĩa là
container đã chết — chạy `docker compose up -d backup`.

```
[2026-09-23 14:15:21] backup: Chưa có bản sao lưu nào trong /backups — chạy ngay.
[2026-09-23 14:15:21] backup: Xong: botcraw_20260923_141521.dump (32 KB)
[2026-09-23 14:45:21] backup: Bản gần nhất mới 0h30p tuổi (ngưỡng 24h) — chưa cần chạy.
```

Chạy một bản ngay lập tức (ví dụ trước khi làm gì đó nguy hiểm) — tự gọi `pg_dump`,
không cần đụng tới vòng lặp:

```bash
docker compose exec backup sh -c 'pg_dump -Fc -f /backups/thucong_truoc_khi_sua.dump'
```

Cố ý **không** đặt tiền tố `botcraw_` cho bản chạy tay: cơ chế xoay vòng chỉ đụng tới
file `botcraw_*.dump`, nên bản này không bao giờ bị tự động xoá — đúng cái bạn muốn ở
một bản chụp "phòng khi tôi làm hỏng".

## KHÔI PHỤC

> Mọi lệnh dưới đây bọc trong `sh -c '...'` là có lý do: chạy trong Git Bash trên
> Windows, một tham số bắt đầu bằng `/` sẽ bị dịch thành đường dẫn Windows, và
> `/backups/x.dump` biến thành `C:/Program Files/Git/backups/x.dump`. Nằm trong chuỗi
> nháy đơn thì nó yên. PowerShell không có vấn đề này nhưng lệnh vẫn chạy đúng.

### Bước 0 — Bắt buộc: thử vào một database tạm trước

Đừng bao giờ restore thẳng đè lên `botcraw`. Nếu file dump hỏng thì thao tác đó phá
luôn dữ liệu đang có, và lúc ấy bạn không còn đường lùi. Kiểm tra trước:

```bash
docker compose exec backup sh -c 'psql -d botcraw -c "CREATE DATABASE botcraw_thu_restore;"'
docker compose exec backup sh -c 'pg_restore --no-owner --exit-on-error -d botcraw_thu_restore /backups/botcraw_20260923_141521.dump'
docker compose exec backup sh -c 'psql -d botcraw_thu_restore -c "select count(*) from places;"'
```

Số dòng ra hợp lý là file dùng được. Dọn database tạm đi:

```bash
docker compose exec backup sh -c 'psql -d postgres -c "DROP DATABASE botcraw_thu_restore;"'
```

Xem nhanh bên trong một file dump mà không restore gì cả — dòng
`Dumped from database version` cho biết bản dump này thuộc về server nào:

```bash
docker compose exec backup sh -c 'pg_restore --list /backups/botcraw_20260923_141521.dump' | head -20
```

### Khôi phục thật, đè lên `botcraw`

**Thao tác này xoá sạch dữ liệu hiện tại của `botcraw`.** Chỉ làm khi dữ liệu đang
có đã hỏng hoặc đã mất.

Dừng mọi thứ đang ghi vào DB trước, nếu không `DROP DATABASE` sẽ báo *"database is
being accessed by other users"* — và nguy hiểm hơn là worker có thể ghi đè lên dữ
liệu vừa khôi phục:

```bash
docker compose stop api worker backup
```

Tạo lại database rỗng rồi nạp file dump vào:

```bash
docker compose exec -T db psql -U botcraw -d postgres -c "DROP DATABASE botcraw;"
docker compose exec -T db psql -U botcraw -d postgres -c "CREATE DATABASE botcraw OWNER botcraw;"
docker compose run --rm --entrypoint sh backup -c 'pg_restore --no-owner --exit-on-error -d botcraw /backups/botcraw_20260923_141521.dump'
```

Dùng `run --rm` chứ không phải `exec` vì service `backup` vừa bị dừng ở bước trên;
`run` dựng một container mới từ đúng image đó, chạy xong tự xoá.

Đổi tên file trong lệnh cuối thành bản bạn muốn lùi về.

Bật lại và kiểm tra:

```bash
docker compose up -d
docker compose exec -T db psql -U botcraw -d botcraw -c "select count(*) from places;"
```

**Không cần chạy `alembic upgrade head` sau khi restore**: bản dump đã chứa cả bảng
`alembic_version`, nên schema và mốc migration khớp nhau sẵn. Chỉ chạy alembic nếu
bạn đang restore một bản dump CŨ vào một bản mã nguồn MỚI hơn — lúc đó
`docker compose up -d` tự làm việc đó qua service `migrate`.

### Khôi phục sang một máy khác

File `.dump` là thứ duy nhất cần mang theo. Ở máy mới: clone repo, `cp .env.example
.env`, `docker compose up -d --build`, chép file dump vào `backups/`, rồi làm đúng
các bước ở mục trên.

Phiên bản PostgreSQL phải là **16**. Cả `db` lẫn `backup` đều ghim
`postgres:16-alpine` chính vì lý do này: `pg_dump` chỉ bảo đảm làm việc với server
cùng major version, và một file dump tạo bằng client lệch version vẫn trông hoàn
toàn bình thường cho tới đúng lúc bạn cần nó.

## Cấu hình

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `BCD_BACKUP_EVERY_HOURS` | 24 | Bản gần nhất cũ hơn ngần này giờ thì chạy bù |
| `BCD_BACKUP_KEEP` | 14 | Số bản giữ lại; cũ hơn thì xoá |
| `BCD_TIMEZONE` | `Asia/Ho_Chi_Minh` | Múi giờ dùng trong tên file và log |

Sửa trong `.env` rồi `docker compose up -d backup` để container nhận giá trị mới.

Hai chi tiết nhỏ nhưng là lý do tồn tại của phần lớn đoạn script:

- **Dump ra file `.tmp` rồi mới đổi tên.** Tắt máy giữa chừng thì thứ còn lại là một
  file `.tmp` — nhìn là biết bỏ đi, và lần chạy sau tự dọn. Ghi thẳng vào tên cuối
  sẽ để lại một bản dump cụt mang đúng tên của một bản sao lưu hợp lệ.
- **Xoay vòng chỉ chạy SAU KHI bản mới đã ghi xong.** Dọn trước rồi dump lỗi là mất
  cả bản mới lẫn bản cũ trong cùng một nhịp.
