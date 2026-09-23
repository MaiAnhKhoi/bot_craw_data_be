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
from app.modules.scraper.place.entity import PLACE_PENDING, Place
from app.modules.scraper.place.writer import PlaceWriter, needs_detail
from app.modules.scraper.status.repository import WorkerStatusRepository

_stop = asyncio.Event()


class JobInterrupted(Exception):
    """Người dùng tạm dừng/huỷ job, hoặc worker phải lùi vì bị chặn."""

    def __init__(self, reason: str, requeue: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.requeue = requeue


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

    # ---------- pha 1: tìm kiếm ----------
    async def run_search_phase(self, page, job_id: int, params: dict) -> None:  # noqa: ANN001
        self.phase = "search"
        self._bump_job(job_id, phase="search")
        while True:
            self._assert_job_runnable(job_id)
            db = SessionLocal()
            try:
                pending = JobRepository(db).pending_queries(job_id)
                if not pending:
                    return
                jq = pending[0]
                jq.status = "running"
                jq.started_at = datetime.now(UTC)
                query_text = jq.query
                query_id = jq.id
                db.commit()
            finally:
                db.close()

            try:
                cards = await search_query(
                    page, query_text, self.s, int(params.get("max_results_per_query", 200))
                )
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

            new_count = 0
            db = SessionLocal()
            try:
                writer = PlaceWriter(db, params.get("region", "VN"))
                for card in cards:
                    _, is_new = writer.upsert_from_card(card, job_id, query_text)
                    new_count += int(is_new)
            finally:
                db.close()

            self._reset_query(query_id, "done", results_found=len(cards))
            self._bump_job(
                job_id,
                inc_done_queries=1,
                inc_total_places=len(cards),
                inc_new_places=new_count,
            )
            self.pacer.on_success()
            self.heartbeat()
            await self.pacer.wait()

    def _reset_query(
        self, query_id: int, status: str, error: str | None = None, results_found: int | None = None
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
                pending = writer.places_of_job(job_id, (PLACE_PENDING,))
                if not pending:
                    return
                place = pending[0]
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
                    PlaceWriter(db, region).apply_detail(target, detail)
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
                self.phase = "idle"
                self.current_job_id = None
                self.heartbeat()
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
