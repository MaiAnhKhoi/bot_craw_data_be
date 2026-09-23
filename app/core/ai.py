"""CHỖ DUY NHẤT gọi AI trong toàn dự án.

Mọi lời gọi mô hình đi qua đây để: khoá một nơi đổi model/khoá API, một nơi xử
lý lỗi, và một nơi để tắt hẳn. Module nghiệp vụ không bao giờ import `anthropic`
trực tiếp.

Nguyên tắc vận hành — AI hỏng KHÔNG ĐƯỢC làm gián đoạn việc cào:
  * Chưa cấu hình khoá  -> `is_enabled()` trả False, giao diện ẩn nút dịch.
  * Gọi lỗi / hết hạn mức -> ném `AiUnavailable`, tầng trên bắt và đi tiếp bằng
    từ khoá gốc. Không có đường nào để AI làm chết một job đang chạy.
"""
from __future__ import annotations

from functools import lru_cache

from loguru import logger
from pydantic import BaseModel, Field

from app.core.config import get_settings


class AiUnavailable(RuntimeError):
    """Không gọi được mô hình (chưa bật, sai khoá, quá hạn mức, mạng lỗi...)."""


class KeywordSuggestion(BaseModel):
    country_code: str = Field(description="Mã quốc gia ISO alpha-2, viết hoa")
    language: str = Field(description="Ngôn ngữ của các từ khoá, ví dụ 'th', 'en'")
    keywords: list[str] = Field(description="Từ khoá người bản địa thực sự gõ vào Google Maps")


class KeywordLocalization(BaseModel):
    results: list[KeywordSuggestion]


_SYSTEM = """Bạn giúp một công ty Việt Nam tìm doanh nghiệp trên Google Maps ở nước ngoài.

Với mỗi quốc gia, hãy đưa ra các CỤM TỪ NGƯỜI BẢN ĐỊA THỰC SỰ GÕ vào ô tìm kiếm
Google Maps để ra đúng loại hình kinh doanh đó. Đây KHÔNG phải bài dịch:
- Dịch nghĩa đen thường ra cụm vô nghĩa với Google Maps ("vựa trái cây" dịch thẳng
  sang tiếng Anh không ai tìm như vậy; người ta gõ "fruit wholesaler").
- Ưu tiên tên NGÀNH NGHỀ mà Google Maps dùng để phân loại địa điểm.
- Với nước không dùng chữ Latinh, đưa cả cụm bản ngữ lẫn cụm tiếng Anh tương đương,
  vì nhiều doanh nghiệp ở đó đặt tên hoặc gắn nhãn bằng tiếng Anh.
- Mỗi quốc gia tối đa 4 cụm, ngắn gọn, không kèm tên thành phố hay quốc gia.
- Không giải thích gì thêm."""


@lru_cache(maxsize=1)
def _client():  # noqa: ANN202
    settings = get_settings()
    if not settings.ai_api_key:
        raise AiUnavailable("Chưa cấu hình BCD_AI_API_KEY")
    import anthropic

    return anthropic.Anthropic(api_key=settings.ai_api_key, max_retries=2, timeout=60.0)


def is_enabled() -> bool:
    settings = get_settings()
    return bool(settings.ai_enabled and settings.ai_api_key)


def localize_keywords(keywords: list[str], countries: list[dict]) -> list[KeywordSuggestion]:
    """Sinh từ khoá tìm kiếm bản địa cho từng quốc gia.

    `countries` là các bản ghi quốc gia của module geo (cần `code` và `name_en`).
    Ném `AiUnavailable` khi không gọi được — nơi gọi phải đi tiếp bằng từ khoá gốc.
    """
    if not is_enabled():
        raise AiUnavailable("Tính năng AI đang tắt")
    if not keywords or not countries:
        return []

    settings = get_settings()
    danh_sach = "\n".join(f"- {c['code']}: {c.get('name_en') or c['name']}" for c in countries)
    prompt = (
        "Loại hình kinh doanh cần tìm, mô tả bằng tiếng Việt:\n"
        + "\n".join(f"- {k}" for k in keywords)
        + f"\n\nCác quốc gia cần từ khoá:\n{danh_sach}"
    )

    try:
        import anthropic

        response = _client().messages.parse(
            model=settings.ai_model,
            max_tokens=4096,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=KeywordLocalization,
        )
    except AiUnavailable:
        raise
    except anthropic.AuthenticationError as exc:
        raise AiUnavailable("Khoá API không hợp lệ") from exc
    except anthropic.RateLimitError as exc:
        raise AiUnavailable("Đã chạm hạn mức gọi AI, thử lại sau") from exc
    except anthropic.APIStatusError as exc:
        raise AiUnavailable(f"Dịch vụ AI trả lỗi {exc.status_code}") from exc
    except anthropic.APIConnectionError as exc:
        raise AiUnavailable("Không kết nối được tới dịch vụ AI") from exc
    except Exception as exc:  # noqa: BLE001 — không để bất kỳ lỗi lạ nào rò ra ngoài
        logger.warning("Gọi AI lỗi lạ: {}", exc)
        raise AiUnavailable("Gọi AI thất bại") from exc

    parsed = response.parsed_output
    if parsed is None:
        raise AiUnavailable("AI trả về dữ liệu không đúng định dạng")

    # Chuẩn hoá: mã quốc gia viết hoa, bỏ cụm rỗng/trùng, giới hạn số lượng.
    out: list[KeywordSuggestion] = []
    for item in parsed.results:
        seen: list[str] = []
        for kw in item.keywords:
            kw = " ".join((kw or "").split())
            if kw and kw.lower() not in {x.lower() for x in seen}:
                seen.append(kw)
        if seen:
            out.append(
                KeywordSuggestion(
                    country_code=item.country_code.upper(),
                    language=item.language,
                    keywords=seen[: settings.ai_max_keywords],
                )
            )
    logger.info("AI sinh từ khoá cho {} quốc gia ({} token vào)", len(out), response.usage.input_tokens)
    return out
