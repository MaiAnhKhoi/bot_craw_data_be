"""Worker cào — MỘT luồng, MỘT Chromium, MỘT BrowserContext.

Vì sao một luồng: máy chạy ở văn phòng và đi ra internet bằng IP của công ty,
không có proxy để xoay. Thứ duy nhất điều khiển được là TỐC ĐỘ, nên chạy chậm và
đều là chiến lược chống chặn chính, còn song song hoá chỉ làm tăng rủi ro mà
không tăng được sản lượng thực (xem docs/ANTI_BLOCK.md).

Hàng đợi nằm trong Postgres (`FOR UPDATE SKIP LOCKED`) nên sau này thêm worker
thứ hai ở máy khác/IP khác là chạy được ngay, không cần message broker.

Chạy:  python -m app.workers.runner
"""
from __future__ import annotations

import asyncio
import signal
from collections import deque
from datetime import UTC, datetime

from loguru import logger
from starlette.concurrency import run_in_threadpool

from app.core import ai
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.logging_config import setup_logging
from app.modules.scraper.engine.browser import (
    BlockedError,
    human_delay,
    launch_context,
    new_page,
)
from app.modules.scraper.engine.detail import DetailParseError, scrape_detail
from app.modules.scraper.engine.models import STOP_RECENT, CardResult
from app.modules.scraper.engine.pacing import Pacer, in_night_rest
from app.modules.scraper.engine.search import search_query
from app.modules.scraper.engine.website import check_many
from app.modules.scraper.job.entity import (
    JOB_CANCELLED,
    JOB_DONE,
    JOB_FAILED,
    JOB_PAUSED,
    JOB_QUEUED,
)
from app.modules.scraper.job.repository import JobRepository
from app.modules.scraper.place.entity import Place
from app.modules.scraper.place.relevance import LIEN_QUAN, NGHI_RAC, RANH_GIOI
from app.modules.scraper.place.relevance import cham as cham_lien_quan
from app.modules.scraper.place.writer import (
    PlaceWriter,
    danh_muc_cho_place,
    needs_detail,
    region_of,
)
from app.modules.scraper.status.repository import WorkerStatusRepository

_stop = asyncio.Event()


class JobInterrupted(Exception):
    """Người dùng tạm dừng/huỷ job, hoặc worker phải lùi vì bị chặn."""

    def __init__(self, reason: str, requeue: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.requeue = requeue


def _danh_muc_cua(params: dict, place: Place) -> list[str]:
    """Danh mục ngành nghề áp cho một địa điểm, tra theo QUỐC GIA CỦA NÓ.

    Pha chi tiết không gắn với một truy vấn nào nên không có `gl` để dùng như pha
    tìm kiếm — nhưng lúc này địa điểm đã có `country_code` chốt từ địa chỉ hoặc
    toạ độ, tức là nguồn còn chắc hơn `gl`.
    """
    ma = (place.country_code or "").upper()
    return (params.get("category_map") or {}).get(ma) or []


class Runner:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.pacer = Pacer(
            base_min=settings.pace_min_seconds,
            base_max=settings.pace_max_seconds,
            ceiling=settings.pace_ceiling_seconds,
            probe_after=settings.block_probe_after_pages,
        )
        self.recent_pages: deque[datetime] = deque(maxlen=2000)
        self.current_job_id: int | None = None
        self.phase = "idle"

    # ---------- nhịp tim ----------
    def _pages_last_hour(self) -> int:
        cutoff = datetime.now(UTC).timestamp() - 3600
        while self.recent_pages and self.recent_pages[0].timestamp() < cutoff:
            self.recent_pages.popleft()
        return len(self.recent_pages)

    def heartbeat(self, *, blocked_increment: int = 0, night_rest: bool = False) -> None:
        db = SessionLocal()
        try:
            WorkerStatusRepository(db).heartbeat(
                job_id=self.current_job_id,
                phase=self.phase,
                pace_seconds=self.pacer.current_seconds,
                pages_last_hour=self._pages_last_hour(),
                night_rest=night_rest,
                blocked_increment=blocked_increment,
            )
        except Exception as exc:  # noqa: BLE001 — nhịp tim hỏng không được làm chết worker
            logger.warning("Ghi nhịp tim lỗi: {}", exc)
            db.rollback()
        finally:
            db.close()

    # ---------- kiểm soát vòng đời job ----------
    def _assert_job_runnable(self, job_id: int) -> dict:
        """Đọc lại trạng thái job từ DB (người dùng có thể vừa bấm tạm dừng trên web)."""
        db = SessionLocal()
        try:
            job = JobRepository(db).by_id(job_id)
            if job is None:
                raise JobInterrupted("job đã bị xoá")
            if job.status == JOB_PAUSED:
                raise JobInterrupted("người dùng tạm dừng")
            if job.status == JOB_CANCELLED:
                raise JobInterrupted("người dùng huỷ")
            return dict(job.params or {})
        finally:
            db.close()

    def _bump_job(self, job_id: int, **fields) -> None:  # noqa: ANN003
        db = SessionLocal()
        try:
            job = JobRepository(db).by_id(job_id)
            if job is None:
                return
            for key, value in fields.items():
                if key.startswith("inc_"):
                    name = key[4:]
                    setattr(job, name, (getattr(job, name) or 0) + value)
                else:
                    setattr(job, key, value)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cập nhật job lỗi: {}", exc)
            db.rollback()
        finally:
            db.close()

    async def _handle_block(self, job_id: int, exc: BlockedError) -> None:
        """Bị chặn: nghỉ dài, trả job về hàng đợi, rồi để vòng ngoài thử lại sau.

        Không đặt job sang `failed`: bị chặn là chuyện tạm thời, công việc đã làm
        vẫn còn nguyên trong DB và sẽ chạy tiếp được.
        """
        self.pacer.on_block()
        self._bump_job(job_id, inc_blocked_count=1)
        self.heartbeat(blocked_increment=1)
        logger.error("Bị Google chặn ({}). Nghỉ {} giây rồi thử lại.", exc, self.s.block_backoff_seconds)
        await asyncio.sleep(self.s.block_backoff_seconds)
        raise JobInterrupted(f"bị chặn: {exc}", requeue=True)

    async def _phan_xu(
        self, params: dict, ranh_gioi: list[CardResult], query_text: str
    ) -> dict[int, ai.PhanXet]:
        """Nhờ AI phán những thẻ luật cứng không quyết nổi. Trả dict theo chỉ số.

        NGOẠI LỆ DUY NHẤT của luật "worker không bao giờ gọi AI", và nó giữ đúng
        tinh thần của luật đó: mọi đường hỏng đều trả về dict RỖNG chứ không ném
        lỗi ra ngoài, và dict rỗng nghĩa là "giữ lại kèm dấu nghi ngờ". Không có
        đường nào để AI làm chết một lượt quét.

        Chạy qua threadpool vì SDK của Anthropic là ĐỒNG BỘ. Gọi thẳng trong
        `async def` sẽ chặn event loop suốt mấy giây mỗi truy vấn.
        """
        settings = get_settings()
        if not ranh_gioi or not settings.ai_judge_enabled or not ai.is_enabled():
            return {}

        # Vượt trần thì cắt bớt chứ không bỏ cả lô: phán được bao nhiêu hay bấy
        # nhiêu, phần dư rơi về "chưa phán được" và vẫn được giữ lại.
        lo = ranh_gioi[: settings.ai_judge_max_per_query]
        mat_hang = list(params.get("keywords") or [])
        ung_vien = [
            ai.UngVien(
                stt=i,
                name=c.name or "",
                category=c.category,
                address=c.address_short,
            )
            for i, c in enumerate(lo)
        ]
        try:
            return await run_in_threadpool(ai.judge_places, mat_hang, ung_vien)
        except ai.AiUnavailable as exc:
            logger.warning("Không phân xử được {} thẻ của '{}': {}", len(lo), query_text, exc)
            return {}
        except Exception as exc:  # noqa: BLE001 — tuyệt đối không để lỗi lạ giết job
            logger.warning("Lỗi lạ khi phân xử thẻ của '{}': {}", query_text, exc)
            return {}

    # ---------- pha 1: tìm kiếm ----------
    async def run_search_phase(self, page, job_id: int, params: dict) -> None:  # noqa: ANN001
        self.phase = "search"
        self._bump_job(job_id, phase="search")
        ttl_days = int(params.get("ttl_days", 90))
        skip_recent = bool(params.get("skip_recent_queries", True))
        while True:
            self._assert_job_runnable(job_id)
            db = SessionLocal()
            try:
                repo = JobRepository(db)
                jq = repo.next_pending_query(job_id)
                if jq is None:
                    return
                query_text = jq.query
                query_id = jq.id
                query_hl, query_gl = jq.hl, jq.gl
                da_quet_luc = (
                    repo.recently_scraped_at(query_text, ttl_days, query_id, query_gl)
                    if skip_recent
                    else None
                )
                if da_quet_luc is not None:
                    jq.status = "skipped"
                    jq.stop_reason = STOP_RECENT
                    jq.results_found = 0
                    jq.finished_at = datetime.now(UTC)
                else:
                    jq.status = "running"
                    jq.started_at = datetime.now(UTC)
                db.commit()
            finally:
                db.close()

            if da_quet_luc is not None:
                logger.info("[{}] bỏ qua — đã quét xong lúc {}", query_text, da_quet_luc)
                self._bump_job(job_id, inc_done_queries=1)
                # KHÔNG gọi `pacer.wait()`: giãn nhịp là để Google khỏi nghi ngờ,
                # mà lượt này không hề chạm tới Google. Ngủ ở đây thì cơ chế bỏ
                # qua mất sạch ý nghĩa — 3.000 truy vấn bỏ qua vẫn tốn cả giờ.
                continue

            try:
                outcome = await search_query(
                    page,
                    query_text,
                    self.s,
                    int(params.get("max_results_per_query", 200)),
                    hl=query_hl,
                    gl=query_gl,
                )
                cards = outcome.cards
            except BlockedError as exc:
                self._reset_query(query_id, "pending")
                await self._handle_block(job_id, exc)
                return
            except Exception as exc:  # noqa: BLE001 — một truy vấn hỏng không được giết cả job
                logger.warning("Truy vấn '{}' lỗi: {}", query_text, exc)
                self.pacer.on_suspicion()
                self._reset_query(query_id, "failed", str(exc))
                self._bump_job(job_id, inc_done_queries=1)
                await self.pacer.wait()
                continue

            # Danh mục ngành nghề của ĐÚNG quốc gia truy vấn này, chốt từ lúc tạo
            # job. Rỗng -> không lọc gì, ghi hết.
            cho_phep = (params.get("category_map") or {}).get((query_gl or "").upper()) or []

            # Bước 1: chấm bằng luật cứng, tách riêng những thẻ luật không quyết nổi.
            #
            # Làm thành ba bước chứ không ghi thẳng trong một vòng, vì bước 2 gọi
            # AI theo LÔ. Gọi từng thẻ một thì 20 thẻ ranh giới là 20 lượt gọi và
            # 20 lần chờ mạng chen vào giữa lúc quét.
            chac_chan: list[tuple[CardResult, str | None]] = []
            ranh_gioi: list[CardResult] = []
            for card in cards:
                diem = cham_lien_quan(card.category, card.name, cho_phep)
                if diem == RANH_GIOI:
                    ranh_gioi.append(card)
                else:
                    chac_chan.append((card, diem))

            # Bước 2: hỏi AI cho riêng phần ranh giới.
            phan_xet = await self._phan_xu(params, ranh_gioi, query_text)

            # Bước 3: ghi tất cả, phần ranh giới đi kèm phán xét (nếu có).
            new_count = 0
            bi_loai = 0
            db = SessionLocal()
            try:
                writer = PlaceWriter(db, params.get("region", "VN"))

                # (thẻ, điểm, nguồn, lý do) — gộp hai nhóm rồi ghi một lượt để
                # đường ghi chỉ có đúng một chỗ, không phải hai bản sao lệch nhau.
                de_ghi = [
                    (card, diem, "rule" if diem is not None else None, None)
                    for card, diem in chac_chan
                ]
                for i, card in enumerate(ranh_gioi):
                    px = phan_xet.get(i)
                    if px is None:
                        # AI tắt, hỏng, hoặc trả thiếu mục này. GIỮ LẠI kèm dấu
                        # nghi ngờ — một lần mạng chập chờn không được phép biến
                        # thành một lần mất lead vĩnh viễn. Người dùng lọc
                        # "Chưa chắc chắn" là thấy hết những dòng này.
                        de_ghi.append(
                            (card, RANH_GIOI, "rule", "Chưa phân xử được — AI không trả lời")
                        )
                    else:
                        de_ghi.append((card, LIEN_QUAN if px.giu else NGHI_RAC, "ai", px.ly_do))

                for card, diem, nguon, ly_do in de_ghi:
                    # `query_gl` là quốc gia đã tìm ra thẻ này — dùng để đọc đúng
                    # số điện thoại nội địa khi địa chỉ chưa đủ để suy ra nước.
                    place, is_new = writer.upsert_from_card(
                        card, job_id, query_text, country_code=query_gl,
                        cho_phep=cho_phep, diem=diem, nguon=nguon, ly_do=ly_do,
                    )
                    if place is None:
                        # Ngoài danh mục ngành nghề -> đã ghi vết ở `place_rejects`.
                        bi_loai += 1
                        continue
                    new_count += int(is_new)

                # Đếm theo số liên kết job-địa điểm THẬT, không cộng dồn len(cards):
                # nhiều truy vấn giao nhau sẽ trả về cùng một địa điểm, cộng dồn sẽ
                # thổi phồng mẫu số và thanh tiến độ không bao giờ tới 100%.
                total_places = writer.count_places_of_job(job_id)
            finally:
                db.close()

            if bi_loai or ranh_gioi:
                logger.info(
                    "Truy vấn '{}': {}/{} thẻ bị loại, {} thẻ ranh giới ({} được AI phán)",
                    query_text, bi_loai, len(cards), len(ranh_gioi), len(phan_xet),
                )
            # `results_found` đếm thứ GOOGLE TRẢ VỀ, không trừ phần bị loại. Đây là
            # con số quyết định "địa bàn này còn sót không" (chạm trần ~120 nghĩa là
            # Google cắt); trừ đi phần mình tự loại sẽ làm một tỉnh đã bị cắt trông
            # như chưa đầy, và nó biến mất khỏi danh sách địa bàn còn sót.
            self._reset_query(
                query_id, "done", results_found=len(cards), stop_reason=outcome.stop_reason
            )
            self._bump_job(
                job_id,
                inc_done_queries=1,
                total_places=total_places,
                inc_new_places=new_count,
            )
            self.pacer.on_success()
            self.heartbeat()
            await self.pacer.wait()

    def _reset_query(
        self,
        query_id: int,
        status: str,
        error: str | None = None,
        results_found: int | None = None,
        stop_reason: str | None = None,
    ) -> None:
        db = SessionLocal()
        try:
            from app.modules.scraper.job.entity import JobQuery

            jq = db.get(JobQuery, query_id)
            if jq is None:
                return
            jq.status = status
            jq.error = (error or None) if error else None
            if results_found is not None:
                jq.results_found = results_found
            # Xoá lý do cũ khi truy vấn được đưa về hàng chờ, nếu không bản chạy lại
            # sẽ mang nhãn của lần chạy trước.
            jq.stop_reason = stop_reason
            if status in ("done", "failed"):
                jq.finished_at = datetime.now(UTC)
            db.commit()
        finally:
            db.close()

    # ---------- pha 2: trang chi tiết ----------
    async def run_detail_phase(self, page, job_id: int, params: dict) -> None:  # noqa: ANN001
        self.phase = "detail"
        self._bump_job(job_id, phase="detail")
        detail_mode = params.get("detail_mode", "missing_only")
        enrich = bool(params.get("enrich_website", True))
        ttl_days = int(params.get("ttl_days", 90))
        region = params.get("region", "VN")

        while True:
            self._assert_job_runnable(job_id)
            db = SessionLocal()
            try:
                writer = PlaceWriter(db, region)
                place = writer.next_pending_of_job(job_id)
                if place is None:
                    # Chốt lại bằng số đếm thật: địa điểm đã `done` từ job trước
                    # không đi qua vòng lặp này nên bộ đếm cộng dồn sẽ thiếu.
                    self._bump_job(job_id, done_places=writer.count_done_places_of_job(job_id))
                    return
                place_id, url, name = place.id, place.maps_url, place.name

                if not needs_detail(place, detail_mode, enrich, ttl_days) or not url:
                    writer.finish_without_detail(place)
                    self._bump_job(job_id, inc_done_places=1)
                    continue
            finally:
                db.close()

            try:
                detail = await scrape_detail(page, url, self.s.page_timeout_ms)
            except BlockedError as exc:
                await self._handle_block(job_id, exc)
                return
            except DetailParseError as exc:
                self.pacer.on_suspicion()
                self._fail_place(place_id, region, str(exc))
                self._bump_job(job_id, inc_failed_places=1)
                await self.pacer.wait()
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("Trang chi tiết '{}' lỗi: {}", name, exc)
                self.pacer.on_suspicion()
                self._fail_place(place_id, region, f"{type(exc).__name__}: {exc}")
                self._bump_job(job_id, inc_failed_places=1)
                await self.pacer.wait()
                continue

            db = SessionLocal()
            try:
                target = db.get(Place, place_id)
                if target is not None:
                    PlaceWriter(db, region).apply_detail(
                        target, detail, cho_phep=_danh_muc_cua(params, target)
                    )
            finally:
                db.close()

            self.recent_pages.append(datetime.now(UTC))
            self.pacer.on_success()
            self._bump_job(job_id, inc_done_places=1)
            self.heartbeat()
            await self.pacer.wait()

    def _fail_place(self, place_id: int, region: str, error: str) -> None:
        db = SessionLocal()
        try:
            place = db.get(Place, place_id)
            if place is not None:
                PlaceWriter(db, region).mark_failed(place, error)
        finally:
            db.close()

    # ---------- pha 3: kiểm tra website ----------
    async def run_enrich_phase(self, job_id: int, params: dict) -> None:
        if not params.get("enrich_website", True):
            return
        self.phase = "enrich"
        self._bump_job(job_id, phase="enrich")
        db = SessionLocal()
        try:
            writer = PlaceWriter(db, params.get("region", "VN"))
            places = writer.places_needing_website_check(job_id)
            if not places:
                return
            urls = [p.website for p in places if p.website]
            logger.info("Kiểm tra {} website doanh nghiệp", len(urls))
            # Không đụng tới Google nên chạy song song thoải mái.
            statuses = await check_many(
                urls, timeout=self.s.website_check_timeout, concurrency=self.s.website_check_concurrency
            )
            for place in places:
                writer.apply_website_status(place, statuses.get(place.website or "", "UNCHECKED"))
        except Exception as exc:  # noqa: BLE001 — bước làm giàu hỏng không được giết job
            logger.warning("Kiểm tra website lỗi: {}", exc)
            db.rollback()
        finally:
            db.close()
        self.heartbeat()

    # ---------- kiểm tra lại các địa điểm lẻ ----------
    async def run_sweep_phase(self, toi_da: int = 20) -> int:
        """Quét lại những địa điểm đang `pending` mà không thuộc job nào đang chạy.

        Nút "Kiểm tra lại" trên giao diện đặt địa điểm về `pending`. Nhưng pha
        chi tiết chỉ lấy việc qua `next_pending_of_job(job_id)` — tức chỉ trong
        job ĐANG CHẠY. Địa điểm thuộc một job đã xong thì không ai đụng tới, nằm
        ở `pending` vĩnh viễn, và bộ đếm `pending` trên trang Tổng quan cứ phình
        lên. Vòng này là nơi duy nhất nhận số việc đó.

        Chỉ chạy khi worker RẢNH, và mỗi lượt giới hạn `toi_da` để job mới tạo
        không phải chờ hết hàng kiểm tra lại mới được chạy.
        """
        self.phase = "sweep"
        da_lam = 0
        async with launch_context(self.s) as ctx:
            page = await new_page(ctx, self.s.page_timeout_ms)
            while da_lam < toi_da and not _stop.is_set():
                db = SessionLocal()
                try:
                    place = PlaceWriter(db).next_pending_anywhere()
                    if place is None:
                        break
                    place_id, url, name = place.id, place.maps_url, place.name
                    # Không truyền phương án dự phòng của job: vòng này chạy
                    # ngoài mọi job, và `Settings` không có `region`. Địa điểm
                    # lấy từ DB thì gần như luôn đã có `country_code` rồi.
                    region = region_of(place)
                finally:
                    db.close()

                if not url:
                    # Không có URL thì không quét lại được; chốt bằng dữ liệu đang
                    # có để nó thoát khỏi `pending` thay vì kẹt lại mãi.
                    db = SessionLocal()
                    try:
                        target = db.get(Place, place_id)
                        if target is not None:
                            PlaceWriter(db, region).finish_without_detail(target)
                    finally:
                        db.close()
                    da_lam += 1
                    continue

                try:
                    detail = await scrape_detail(page, url, self.s.page_timeout_ms)
                except BlockedError as exc:
                    logger.error("Kiểm tra lại bị chặn: {}", exc)
                    self.pacer.on_block()
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Kiểm tra lại '{}' lỗi: {}", name, exc)
                    self.pacer.on_suspicion()
                    self._fail_place(place_id, region, f"{type(exc).__name__}: {exc}")
                    da_lam += 1
                    await self.pacer.wait()
                    continue

                db = SessionLocal()
                try:
                    target = db.get(Place, place_id)
                    if target is not None:
                        PlaceWriter(db, region).apply_detail(
                            target, detail, cho_phep=danh_muc_cho_place(db, target)
                        )
                finally:
                    db.close()

                self.recent_pages.append(datetime.now(UTC))
                self.pacer.on_success()
                da_lam += 1
                self.heartbeat()
                await self.pacer.wait()
        if da_lam:
            logger.info("Đã kiểm tra lại {} địa điểm", da_lam)
        return da_lam

    def _so_diem_cho_kiem_lai(self) -> int:
        """Đếm trước khi mở trình duyệt — mở Chromium tốn ~1 giây, không đáng bỏ
        ra chỉ để phát hiện là chẳng có việc gì."""
        db = SessionLocal()
        try:
            return PlaceWriter(db).count_pending_anywhere()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Đếm địa điểm chờ kiểm tra lỗi: {}", exc)
            return 0
        finally:
            db.close()

    # ---------- một job ----------
    async def process_job(self, job_id: int) -> None:
        params = self._assert_job_runnable(job_id)
        logger.info("Bắt đầu job #{}", job_id)
        async with launch_context(self.s) as ctx:
            page = await new_page(ctx, self.s.page_timeout_ms)
            await self.run_search_phase(page, job_id, params)
            await self.run_detail_phase(page, job_id, params)
        await self.run_enrich_phase(job_id, params)
        self._bump_job(
            job_id, status=JOB_DONE, phase="idle", finished_at=datetime.now(UTC)
        )
        logger.success("Xong job #{}", job_id)

    # ---------- vòng lặp chính ----------
    async def loop(self) -> None:
        logger.info(
            "Worker khởi động — nhịp nền {}-{}s, nghỉ đêm {}h-{}h, driver {}",
            self.s.pace_min_seconds, self.s.pace_max_seconds,
            self.s.night_rest_start, self.s.night_rest_end, self.s.browser_engine,
        )
        while not _stop.is_set():
            if in_night_rest(datetime.now(), self.s.night_rest_start, self.s.night_rest_end):
                self.phase = "idle"
                self.current_job_id = None
                self.heartbeat(night_rest=True)
                await asyncio.sleep(60)
                continue

            db = SessionLocal()
            try:
                job = JobRepository(db).claim_next()
                job_id = job.id if job else None
                if job is not None:
                    db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Nhận job lỗi: {}", exc)
                db.rollback()
                job_id = None
            finally:
                db.close()

            if job_id is None:
                self.current_job_id = None
                # Rảnh job thì mới quay sang hàng chờ "kiểm tra lại".
                da_lam = 0
                if self._so_diem_cho_kiem_lai():
                    try:
                        da_lam = await self.run_sweep_phase()
                    except Exception as exc:  # noqa: BLE001 — không được giết worker
                        logger.warning("Vòng kiểm tra lại lỗi: {}", exc)
                self.phase = "idle"
                self.heartbeat()
                if not da_lam:
                    await asyncio.sleep(self.s.worker_poll_seconds)
                continue

            self.current_job_id = job_id
            try:
                await self.process_job(job_id)
            except JobInterrupted as exc:
                logger.warning("Job #{} dừng giữa chừng: {}", job_id, exc.reason)
                if exc.requeue:
                    self._bump_job(job_id, status=JOB_QUEUED, phase="idle")
            except Exception as exc:  # noqa: BLE001 — job hỏng không được giết worker
                logger.exception("Job #{} lỗi: {}", job_id, exc)
                self._bump_job(
                    job_id,
                    status=JOB_FAILED,
                    phase="idle",
                    last_error=f"{type(exc).__name__}: {exc}"[:500],
                    finished_at=datetime.now(UTC),
                )
            finally:
                self.current_job_id = None
                self.phase = "idle"
                self.heartbeat()
            await human_delay(2, 5)


def _install_signal_handlers() -> None:
    def _handler(*_):  # noqa: ANN002
        logger.info("Nhận tín hiệu dừng, đang kết thúc job hiện tại...")
        _stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except (ValueError, AttributeError):
            pass   # Windows không có đủ tín hiệu; bỏ qua


async def main() -> None:
    setup_logging("worker")
    _install_signal_handlers()
    await Runner(get_settings()).loop()


if __name__ == "__main__":
    asyncio.run(main())
