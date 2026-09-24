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

import unicodedata
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
    categories: list[str] = Field(
        default_factory=list,
        description=(
            "Tên NGÀNH NGHỀ Google Maps gắn cho loại hình này ở nước đó, cả bản ngữ "
            "lẫn tiếng Anh. Dùng để lọc rác SAU khi quét, không dùng để tìm."
        ),
    )


class KeywordLocalization(BaseModel):
    results: list[KeywordSuggestion]


_SYSTEM = """Bạn giúp một công ty Việt Nam tìm doanh nghiệp trên Google Maps ở nước ngoài.

Với mỗi quốc gia trả về HAI danh sách, dùng cho hai việc khác hẳn nhau.

1) `keywords` — CỤM TỪ NGƯỜI BẢN ĐỊA THỰC SỰ GÕ vào ô tìm kiếm Google Maps.
Đây KHÔNG phải bài dịch: dịch nghĩa đen thường ra cụm không ai tìm ("vựa trái cây"
dịch thẳng sang tiếng Anh không ai gõ như vậy; người ta gõ "fruit wholesaler").
- MỖI CỤM PHẢI NÓI RÕ MẶT HÀNG. Cấm cụm chung chung không nêu mặt hàng, kiểu
  "produce supplier", "wholesaler", "food supplier", "distributor", "trading
  company". Đã đo: "produce supplier" trả về 118 kết quả thì 51% là tiệm bánh,
  hiệu sách, đại lý du lịch — Google hiểu quá rộng. Cùng ý đó, "fruit wholesaler"
  chỉ 9% lạc đề.
- Với nước không dùng chữ Latinh, ưu tiên cụm BẢN NGỮ. Chỉ thêm cụm tiếng Anh khi
  tiếng Anh thật sự được dùng phổ biến ở nước đó.
- Mỗi quốc gia tối đa 4 cụm, ngắn gọn, không kèm tên thành phố hay quốc gia.

2) `categories` — TÊN NGÀNH NGHỀ mà Google Maps gắn nhãn cho loại hình này.
Danh sách này KHÔNG dùng để tìm. Nó dùng để LOẠI BỎ kết quả lạc đề sau khi quét,
nên thiếu một nhãn đúng là mất oan doanh nghiệp thật.
- Liệt kê RỘNG TAY: 8–14 nhãn, gồm cả nhãn hẹp ("Fruit wholesaler") lẫn nhãn rộng
  mà doanh nghiệp loại này hay bị gắn ("Grocery store", "Market", "Supermarket",
  "Wholesaler", "Farm").
- BẮT BUỘC HAI VẾ, và vế bản ngữ phải viết bằng ĐÚNG CHỮ VIẾT của nước đó
  (Devanagari, Thái, Hán, Ả Rập...), KHÔNG phiên âm sang chữ Latinh:
    * ít nhất 4 nhãn bằng chữ bản ngữ,
    * ít nhất 4 nhãn bằng tiếng Anh.
  Google hiện nhãn theo ngôn ngữ giao diện, nên cùng một cửa hàng lúc ra
  "फल विक्रेता" lúc ra "Fruit and vegetable store". Thiếu một vế là lọc nhầm hàng
  loạt. Nước nói tiếng Anh thì hai vế trùng nhau, cứ liệt kê tiếng Anh.
- Chỉ ghi nhãn thật sự dính tới mặt hàng hoặc kênh phân phối của nó. KHÔNG thêm
  nhãn ngoài ngành ("Restaurant", "Bakery", "Travel agency") dù chúng có bán kèm.

Không giải thích gì thêm."""


def _lac_chu_viet(term: str) -> bool:
    """Cụm này có lẫn chữ Latinh vào giữa một hệ chữ khác không.

    Mô hình thỉnh thoảng nhả ra cụm hỏng với những hệ chữ ít dữ liệu. Đo thật khi
    xin từ khoá tiếng Khmer: `ឧmartinមាក់ផ្លែឈើ` (có nguyên chữ "martin" nằm
    giữa) và `អ vendor`. Gõ những cụm đó vào Google Maps thì ra con số không, mà
    KHÔNG có lỗi nào báo — job chạy xong, bảng trống, không ai biết vì sao.

    Một cụm thuần Latinh ("Fruit wholesaler") hay thuần bản ngữ ("ផ្លែឈើ") đều
    bình thường; chỉ TRỘN hai hệ chữ trong cùng một cụm mới là dấu hiệu hỏng.
    """
    co_latinh = co_khac = False
    for ch in term:
        if not ch.isalpha():
            continue
        if unicodedata.name(ch, "").startswith("LATIN"):
            co_latinh = True
        else:
            co_khac = True
        if co_latinh and co_khac:
            return True
    return False


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
    def _gon(values: list[str], nhan: str) -> list[str]:
        seen: list[str] = []
        for v in values or []:
            v = " ".join((v or "").split())
            if not v or v.lower() in {x.lower() for x in seen}:
                continue
            if _lac_chu_viet(v):
                logger.warning("Bỏ {} hỏng do lẫn chữ viết: {!r}", nhan, v)
                continue
            seen.append(v)
        return seen

    out: list[KeywordSuggestion] = []
    for item in parsed.results:
        seen = _gon(item.keywords, "từ khoá")
        if seen:
            out.append(
                KeywordSuggestion(
                    country_code=item.country_code.upper(),
                    language=item.language,
                    keywords=seen[: settings.ai_max_keywords],
                    # KHÔNG cắt bớt: danh sách này là lưới lọc, mỗi nhãn bị cắt là
                    # một kiểu doanh nghiệp thật bị loại oan. `ai_max_keywords` chỉ
                    # nói về số cụm ĐI TÌM, không liên quan tới số nhãn để lọc.
                    categories=_gon(item.categories, "danh mục ngành nghề"),
                )
            )
    logger.info("AI sinh từ khoá cho {} quốc gia ({} token vào)", len(out), response.usage.input_tokens)
    return out
# ---------------------------------------------------------------- chấm lại


class UngVien(BaseModel):
    """Một thẻ kết quả cần AI phân xử, rút gọn còn đúng thứ đủ để phán."""

    stt: int = Field(description="Số thứ tự trong lô, để ghép kết quả trả về")
    name: str
    category: str | None = None
    address: str | None = None


class PhanXet(BaseModel):
    stt: int = Field(description="Số thứ tự của ứng viên tương ứng")
    giu: bool = Field(description="True = đúng ngành, giữ lại. False = lạc đề, loại.")
    ly_do: str = Field(description="Một câu ngắn bằng tiếng Việt, tối đa 25 từ")


class PhanXetLo(BaseModel):
    results: list[PhanXet]


_SYSTEM_PHAN_XET = """Bạn lọc danh sách doanh nghiệp lấy từ Google Maps cho một công ty Việt Nam.

Với mỗi mục, trả lời: đây CÓ PHẢI là loại doanh nghiệp đang cần tìm không.

Chỉ có tên, ngành nghề Google gắn và địa chỉ. Những mục này đã bị luật tự động
xếp vào diện KHÔNG QUYẾT ĐƯỢC — hoặc nhãn ngành nghề vô nghĩa ("Company",
"General store"), hoặc nhãn rõ ràng thuộc ngành khác nhưng tên lại dính. Phán
đoán phải dựa chủ yếu vào TÊN DOANH NGHIỆP.

Luật phán:
- GIỮ nếu tên cho thấy doanh nghiệp làm đúng mặt hàng hoặc đúng khâu trong chuỗi
  của mặt hàng đó (trồng, thu mua, chế biến, bán buôn, xuất nhập khẩu, phân phối).
  Ví dụ "Battambang Agro Industry Co., Ltd." với mặt hàng trái cây: GIỮ — doanh
  nghiệp nông sản, nhãn "Company" chỉ là Google gắn chung chung.
- LOẠI nếu ngành nghề rõ ràng là thứ khác và tên chỉ TÌNH CỜ chứa một chữ giống.
  Ví dụ "Dress store", "Book store", "Tourist attraction": LOẠI.
- LOẠI hộ kinh doanh bán lẻ lặt vặt không có dấu hiệu buôn sỉ hay chế biến, nếu
  mặt hàng cần tìm nói rõ là công ty/nhà cung cấp.
- LOẠI hàng ăn uống, kể cả khi tên có nhắc tới mặt hàng: quán cà phê, quán nước
  ép, nhà hàng, tiệm bánh, quầy sinh tố. Họ TIÊU THỤ mặt hàng chứ không cung
  cấp. Ví dụ "M.M Pizza Coffee Fruit Juice" với mặt hàng trái cây: LOẠI — có
  chữ "Fruit Juice" nhưng đó là quán nước, không phải nguồn hàng.
- CHỢ thì xét theo vai trò, và xét NHẤT QUÁN: chợ truyền thống, chợ đầu mối,
  chợ nông sản đều là nơi mua được hàng -> GIỮ. Điểm tham quan mang chữ "market"
  trong tên -> LOẠI.
- KHÔNG QUYẾT ĐƯỢC thì GIỮ. Giữ nhầm một dòng thì người dùng nhìn thấy rồi tự
  loại; loại nhầm một dòng thì nó biến mất khỏi bảng và không ai biết để mà tìm.

`ly_do` viết tiếng Việt, một câu, tối đa 25 từ, nói căn cứ cụ thể chứ đừng nhắc
lại luật. Trả về ĐÚNG MỘT phán xét cho mỗi `stt` nhận vào, không thiếu không thừa."""


def judge_places(mat_hang: list[str], ung_vien: list[UngVien]) -> dict[int, PhanXet]:
    """Phân xử một lô thẻ ranh giới. Trả dict theo `stt`.

    Ném `AiUnavailable` khi không gọi được — nơi gọi PHẢI bắt và đi tiếp, giữ lại
    những thẻ chưa phán được. Đây là điểm khác biệt duy nhất so với luật "worker
    không gọi AI": nó chạy trong lúc quét, nên tuyệt đối không được ném lỗi ra
    ngoài theo cách làm chết job.

    `stt` nào AI bỏ sót thì KHÔNG có trong dict trả về — nơi gọi phải coi đó là
    "chưa phán được" chứ không phải "bị loại". Bịa ra một phán xét mặc định ở đây
    là biến một lần AI trả thiếu thành một lần mất dữ liệu im lặng.
    """
    if not is_enabled():
        raise AiUnavailable("Tính năng AI đang tắt")
    if not ung_vien:
        return {}

    settings = get_settings()
    danh_sach = "\n".join(
        f"{u.stt}. {u.name}"
        + (f" | ngành nghề: {u.category}" if u.category else "")
        + (f" | địa chỉ: {u.address}" if u.address else "")
        for u in ung_vien
    )
    prompt = (
        "Loại doanh nghiệp cần tìm, mô tả bằng tiếng Việt:\n"
        + "\n".join(f"- {k}" for k in mat_hang)
        + f"\n\nDanh sách cần phán ({len(ung_vien)} mục):\n{danh_sach}"
    )

    try:
        import anthropic

        response = _client().messages.parse(
            model=settings.ai_model,
            max_tokens=8192,
            system=_SYSTEM_PHAN_XET,
            messages=[{"role": "user", "content": prompt}],
            output_format=PhanXetLo,
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
    except Exception as exc:  # noqa: BLE001 — không để lỗi lạ nào làm chết job đang quét
        logger.warning("Gọi AI phân xử lỗi lạ: {}", exc)
        raise AiUnavailable("Gọi AI thất bại") from exc

    parsed = response.parsed_output
    if parsed is None:
        raise AiUnavailable("AI trả về dữ liệu không đúng định dạng")

    hop_le = {u.stt for u in ung_vien}
    ra: dict[int, PhanXet] = {}
    for px in parsed.results:
        # Bỏ `stt` lạ: mô hình bịa ra số không có trong lô thì ghép vào sẽ gán
        # phán xét của mục này cho mục khác — sai lặng lẽ và không lần ra được.
        if px.stt in hop_le and px.stt not in ra:
            ra[px.stt] = PhanXet(stt=px.stt, giu=px.giu, ly_do=" ".join(px.ly_do.split())[:300])
    thieu = len(hop_le) - len(ra)
    logger.info(
        "AI phân xử {} thẻ ranh giới: giữ {}, loại {}, chưa phán {} ({} token vào)",
        len(hop_le), sum(1 for p in ra.values() if p.giu),
        sum(1 for p in ra.values() if not p.giu), thieu, response.usage.input_tokens,
    )
    return ra
