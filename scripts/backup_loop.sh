#!/bin/sh
# =====================================================================
# bot_craw_data — sao lưu PostgreSQL bằng VÒNG LẶP BÙ GIỜ (không phải cron).
#
# ★ VÌ SAO KHÔNG HẸN ĐÚNG 3H SÁNG: máy chạy hệ thống này là MÁY VĂN PHÒNG, tối
#   thường bị tắt. Cron hẹn 3h sáng thì hôm nào máy tắt là hôm đó KHÔNG có bản sao
#   lưu nào — mà không ai biết. Tin rằng mình đang được bảo vệ trong khi thật ra
#   không, còn tệ hơn là biết mình chưa sao lưu.
#
#   Nên ở đây KHÔNG hỏi "đã tới giờ hẹn chưa" mà hỏi "bản gần nhất bao nhiêu tuổi",
#   mỗi 30 phút một lần. Máy bật lúc 9h sáng -> 9h có bản sao lưu, không phải đợi
#   tới 3h sáng hôm sau. Máy chạy 24/7 thì hành vi vẫn đúng như hẹn giờ mỗi 24h.
#
# ★ Chạy trong container `backup` của docker-compose, dùng chính image
#   postgres:16-alpine của service `db`: pg_dump BẮT BUỘC cùng major version với
#   server, lệch version là lúc cần restore mới biết mình không restore được.
#
# Đọc log:  docker compose logs -f backup
# Restore:  xem docs/BACKUP.md
# =====================================================================

# Cố ý KHÔNG dùng `set -e`: một lần pg_dump hỏng (DB đang bận, đĩa đầy, container db
# vừa restart) mà giết luôn vòng lặp thì mất hẳn cơ chế sao lưu cho tới khi có người
# để ý. Lỗi được bắt tại chỗ, ghi log, rồi vòng lặp thử lại ở nhịp sau.
set -u

BACKUP_DIR=/backups
PREFIX=botcraw

# Chu kỳ hỏi "đã tới lúc chưa". 30 phút: đủ dày để bật máy lúc nào cũng có bản sao lưu
# trong vòng nửa giờ, đủ thưa để log không biến thành rác. Không có lý do chỉnh nên
# cố định ở đây chứ không bày thêm một biến nữa ra .env.
CHECK_SECONDS=1800

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] backup: $*"
}

# Biến môi trường do người dùng khai, nên phải coi là rác cho tới khi kiểm tra xong:
# BCD_BACKUP_EVERY_HOURS=24h (gõ nhầm chữ "h") sẽ làm phép tính số học dưới kia chết
# mỗi vòng lặp, và cách nó chết là không sao lưu gì cả.
so_nguyen_hoac_mac_dinh() {
  gia_tri="$1"
  mac_dinh="$2"
  ten="$3"
  case "$gia_tri" in
    '' | *[!0-9]*)
      log "CẢNH BÁO: $ten='$gia_tri' không phải số nguyên, dùng mặc định $mac_dinh."
      echo "$mac_dinh"
      ;;
    *)
      if [ "$gia_tri" -lt 1 ]; then
        log "CẢNH BÁO: $ten='$gia_tri' phải >= 1, dùng mặc định $mac_dinh."
        echo "$mac_dinh"
      else
        echo "$gia_tri"
      fi
      ;;
  esac
}

EVERY_HOURS=$(so_nguyen_hoac_mac_dinh "${BCD_BACKUP_EVERY_HOURS:-24}" 24 BCD_BACKUP_EVERY_HOURS)
KEEP=$(so_nguyen_hoac_mac_dinh "${BCD_BACKUP_KEEP:-14}" 14 BCD_BACKUP_KEEP)

PGHOST="${PGHOST:-db}"
PGUSER="${PGUSER:-botcraw}"
PGDATABASE="${PGDATABASE:-botcraw}"
export PGHOST PGUSER PGDATABASE

# Thời điểm sửa đổi của bản sao lưu MỚI NHẤT, tính bằng epoch. 0 = chưa có bản nào.
# Dựa vào mtime của file chứ không vào tên file: người dùng được khuyến khích copy
# file ra Drive, và thao tác copy/đổi tên tay không được phép làm lệch lịch sao lưu.
epoch_ban_moi_nhat() {
  moi_nhat=0
  for f in "$BACKUP_DIR/${PREFIX}"_*.dump; do
    [ -f "$f" ] || continue
    m=$(stat -c %Y "$f" 2>/dev/null) || continue
    if [ "$m" -gt "$moi_nhat" ]; then
      moi_nhat="$m"
    fi
  done
  echo "$moi_nhat"
}

chay_sao_luu() {
  moc="$(date '+%Y%m%d_%H%M%S')"
  ban_cuoi="$BACKUP_DIR/${PREFIX}_${moc}.dump"
  # ★ Ghi ra file tạm rồi mới đổi tên. Container bị `docker compose stop` hay máy mất
  # điện giữa lúc dump thì cái còn lại là một file .tmp — nhìn là biết bỏ đi. Ghi
  # thẳng vào tên cuối sẽ để lại một bản dump CỤT mang đúng tên bản sao lưu hợp lệ,
  # và người ta chỉ phát hiện ra vào đúng hôm cần restore.
  ban_tam="$ban_cuoi.tmp"

  log "Bắt đầu pg_dump database '$PGDATABASE' -> ${PREFIX}_${moc}.dump"
  # -Fc: định dạng custom — đã nén sẵn, và restore được từng bảng bằng pg_restore.
  # Bản .sql thuần vừa to hơn nhiều lần vừa chỉ restore được kiểu tất-cả-hoặc-không.
  if ! pg_dump -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -Fc -f "$ban_tam"; then
    log "LỖI: pg_dump thất bại. Các bản cũ được GIỮ NGUYÊN, chỉ xoá file tạm."
    rm -f "$ban_tam"
    return 1
  fi

  kich_thuoc=$(stat -c %s "$ban_tam" 2>/dev/null || echo 0)
  # pg_dump có thể trả về 0 mà file rỗng nếu đĩa vừa đầy đúng lúc ghi.
  if [ "$kich_thuoc" -le 0 ]; then
    log "LỖI: file dump rỗng (đĩa đầy?). Bỏ file tạm, giữ nguyên bản cũ."
    rm -f "$ban_tam"
    return 1
  fi

  mv "$ban_tam" "$ban_cuoi" || {
    log "LỖI: không đổi tên được file tạm thành bản chính thức."
    rm -f "$ban_tam"
    return 1
  }

  log "Xong: ${PREFIX}_${moc}.dump ($((kich_thuoc / 1024)) KB)"
  return 0
}

# ★ CHỈ được gọi SAU KHI chay_sao_luu() trả về 0. Xoay vòng trước rồi dump lỗi là
# kịch bản mất sạch dữ liệu: vừa không có bản mới, vừa không còn bản cũ.
xoay_vong() {
  # Tên file do chính script này đặt nên không bao giờ có dấu cách hay xuống dòng —
  # đọc kết quả `ls` bằng vòng lặp ở đây là an toàn.
  ls -1 "$BACKUP_DIR/${PREFIX}"_*.dump 2>/dev/null | sort -r | tail -n +$((KEEP + 1)) | while read -r cu; do
    if rm -f "$cu"; then
      log "Xoá bản cũ vượt hạn mức giữ $KEEP bản: $(basename "$cu")"
    fi
  done
}

trap 'log "Nhận tín hiệu dừng — thoát."; exit 0' INT TERM

log "Khởi động. Ngưỡng tuổi: ${EVERY_HOURS}h · giữ lại: $KEEP bản · kiểm tra mỗi $((CHECK_SECONDS / 60)) phút · thư mục: $BACKUP_DIR"

# Dọn xác file tạm của lần chạy trước bị cắt ngang. Không dọn thì chúng nằm lại vĩnh
# viễn, chiếm đĩa và làm người đọc thư mục hoang mang.
for t in "$BACKUP_DIR"/*.dump.tmp; do
  [ -f "$t" ] || continue
  log "Dọn file dump cụt còn sót từ lần dừng đột ngột: $(basename "$t")"
  rm -f "$t"
done

while true; do
  moi_nhat=$(epoch_ban_moi_nhat)
  bay_gio=$(date '+%s')

  if [ "$moi_nhat" -eq 0 ]; then
    log "Chưa có bản sao lưu nào trong $BACKUP_DIR — chạy ngay."
    chay_sao_luu && xoay_vong
  else
    tuoi_phut=$(((bay_gio - moi_nhat) / 60))
    if [ "$tuoi_phut" -ge $((EVERY_HOURS * 60)) ]; then
      log "Bản gần nhất đã $((tuoi_phut / 60))h$((tuoi_phut % 60))p tuổi, quá ngưỡng ${EVERY_HOURS}h — sao lưu bù ngay."
      chay_sao_luu && xoay_vong
    else
      log "Bản gần nhất mới $((tuoi_phut / 60))h$((tuoi_phut % 60))p tuổi (ngưỡng ${EVERY_HOURS}h) — chưa cần chạy."
    fi
  fi

  # `sleep` nền + `wait` thay vì `sleep` trực tiếp: sh đang chờ một lệnh tiền cảnh sẽ
  # KHÔNG xử lý trap cho tới khi lệnh đó xong, nên `docker compose stop` phải đợi hết
  # timeout rồi SIGKILL. Kiểu này thì container dừng ngay.
  sleep "$CHECK_SECONDS" &
  wait $!
done
