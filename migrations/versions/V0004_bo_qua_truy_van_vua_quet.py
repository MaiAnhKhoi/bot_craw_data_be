"""V0004 - chỉ mục phục vụ việc bỏ qua truy vấn vừa quét gần đây.

Worker hỏi "chuỗi truy vấn này đã chạy xong trong N ngày qua chưa" MỘT LẦN cho
MỖI truy vấn, ở mọi job. Job quét tới cấp phường/xã có 3.321 truy vấn, mà bảng
`job_queries` thì tích luỹ qua mọi lần chạy — không có chỉ mục là 3.321 lần quét
toàn bảng, đủ để biến thứ lẽ ra tiết kiệm thời gian thành thứ tốn thời gian.

Thứ tự cột theo đúng thứ tự lọc: `query` (bằng) -> `status` (bằng) ->
`finished_at` (khoảng), nên Postgres dùng được cả ba mức.

Revision ID: V0004
Revises: V0003
"""
from __future__ import annotations

from alembic import op

revision = "V0004"
down_revision = "V0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_job_queries_query_finished",
        "job_queries",
        ["query", "status", "finished_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_job_queries_query_finished", table_name="job_queries")
