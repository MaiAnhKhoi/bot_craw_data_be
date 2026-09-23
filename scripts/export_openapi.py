"""Xuất đặc tả OpenAPI ra file — KHÔNG cần database đang chạy.

Frontend dùng file này để sinh client bằng Orval:
    python -m scripts.export_openapi ../bot_craw_data_fe/openapi/api-docs.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.main import app


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi/api-docs.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã ghi {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
