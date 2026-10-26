"""Chấm "địa điểm này có đúng ngành nghề mình đang tìm không".

VÌ SAO CẦN — Google Maps không bao giờ trả về một danh sách ngắn. Hết kết quả
khớp thật, nó ĐỘN THÊM những thứ loãng dần ở gần đó cho đủ dài. Đo trên job
"Công ty trái cây" ở quần đảo Andaman (118 kết quả, dừng vì `exhausted`):

    vị trí   1–20   18/20 đúng ngành  (90%)
    vị trí  21–40   17/20             (85%)
    vị trí  41–60   13/20             (65%)
    vị trí  61–80    4/20             (20%)
    vị trí  81–100   4/20             (20%)
    vị trí 101–118   2/18             (11%)

Khúc đuôi là tiệm bánh kem, hiệu sách, cửa hàng quần áo, đại lý du lịch. Chúng
KHÔNG phải lỗi dịch: "Tillai's Bakery & Sweets" được tìm ra bởi `फल व्यापारी`
("người buôn trái cây") — một bản dịch hoàn toàn đúng. Đó là Google độn.

NGUYÊN TẮC — chấm điểm, KHÔNG xoá và KHÔNG chặn lúc ghi.
Ngưỡng ở đây là phỏng đoán. Phỏng đoán sai mà đã xoá thì mất vĩnh viễn, còn
phỏng đoán sai mà chỉ đánh dấu thì người dùng bỏ bộ lọc ra là thấy lại. Mọi thứ
Google trả về vẫn nằm nguyên trong bảng.
"""
from __future__ import annotations

import re
import unicodedata

from app.core.text import fold_text

# Đúng ngành — chắc chắn giữ.
LIEN_QUAN = "match"
# Không thấy dấu hiệu nào dính tới ngành đang tìm. KHÔNG phải "rác chắc chắn":
# chỉ là "chưa chứng minh được liên quan", nên mặc định ẩn chứ không xoá.
NGHI_RAC = "weak"
# Luật cứng không đủ căn cứ để quyết — phải đưa cho AI nhìn tên + nhãn + địa chỉ.
#
# Đây là TOÀN BỘ phần sai còn lại của bộ lọc, đo trên dữ liệu thật:
#   * Nhãn vô nghĩa: `Battambang Agro Industry Co., Ltd.` bị Google gắn nhãn
#     "Company" nên bị loại — gần như chắc chắn là lead thật bị vứt. Có 6 dòng
#     "General store" cùng cảnh ở Andaman.
#   * Chỉ khớp qua TÊN: "Dress store", "Book store", "Tourist attraction" lọt
#     vào chỉ vì tên có một chữ dính ngành. Nhưng cũng chính luật này cứu
#     "Rahim Fruits Stall" bị gắn nhãn "Cửa hàng" — nên không bỏ được, chỉ có
#     thể hỏi kỹ hơn.
#
# Giá trị ngắn hơn 8 ký tự vì cột `places.relevance` là VARCHAR(8).
RANH_GIOI = "unsure"

# Từ có mặt ở gần như mọi tên ngành nghề nên không phân biệt được gì.
#
# Danh sách này cố tình NGẮN. Mỗi từ thêm vào đây là một cách mới để hai chuỗi
# thật sự khác nhau bị coi là giống nhau — sai về phía giữ lại rác thì còn cứu
# được bằng mắt, sai về phía vứt mất lead thì không.
VO_NGHIA = frozenset(
    {
        # tiếng Việt
        "cua", "hang", "cong", "ty", "doanh", "nghiep", "co", "so", "kinh",
        "nha", "dai", "ly", "diem", "tiem", "quan", "trung", "tam", "dich", "vu",
        "va", "cac", "ban",
        # tiếng Anh
        "the", "and", "of", "ltd", "inc", "llc", "pvt", "store", "shop",
        "company", "service", "services", "center", "centre", "agency", "dealer",
        "supplies", "supply", "supplier", "provider", "business", "general",
        # "supplier" nằm đây vì đã đo: allow-list có "Fruit supplier" thì
        # "Building materials supplier", "Printing equipment supplier",
        # "Electrical equipment supplier" đều lọt lưới. Bỏ nó đi thì "Fruit
        # supplier" vẫn còn "fruit" để khớp, không mất gì.
        # CỐ Ý GIỮ LẠI "wholesaler", "market", "trader", "merchant": chúng
        # đứng một mình vẫn là nhãn hợp lệ của dân buôn trái cây ("Wholesaler",
        # "Market"). Loại chúng để chặn "Rice wholesaler" là đổi 2 kết quả rác
        # lấy 10 lead thật.
    }
)


# Từ TIẾNG VIỆT có dấu mà gần như tên ngành nào cũng có, nên không phân biệt được
# gì. Viết CÓ DẤU vì mục đích của cả bộ này là giữ dấu: "cửa hàng" là từ chung
# chung, nhưng "cưa", "hang" thì không. Các từ không dấu ("doanh", "trung", "kinh")
# nằm sẵn trong `VO_NGHIA` ở trên.
VO_NGHIA_DAU = frozenset(
    {
        "cửa", "hàng", "công", "cơ", "sở", "nghiệp", "nhà", "đại", "lý", "điểm",
        "tiệm", "quán", "tâm", "dịch", "vụ", "và", "các", "bán", "của",
        # "đồ" là từ chỉ loại ("đồ gia dụng", "đồ chơi"): để nó lọt vào thì hai
        # ngành chẳng liên quan gì nhau vẫn khớp nhau chỉ vì cùng có chữ "đồ".
        # "chính hãng" là lời quảng cáo gắn vào tên cửa hàng đủ mọi ngành.
        "đồ", "chính", "hãng",
    }
)

_TACH = re.compile(r"[\s\-_/|,.;:()\[\]{}'\"!?&+*#@~=<>]+")


def _la_latinh_co_dau(tu: str) -> bool:
    """Từ viết bằng chữ Latinh và CÓ DẤU (tiếng Việt, Pháp...).

    Chữ Hindi/Thái/Hán không vào đây — chúng đã được `fold_text` phiên âm sang
    Latinh và đi đường cũ, vì tách theo ký tự gốc sẽ cắt vụn chúng ở chỗ dấu
    nguyên âm (`isalnum()` coi dấu đó không phải chữ).
    """
    if all(ord(c) < 128 for c in tu):
        return False
    return all(
        unicodedata.name(c, "").startswith("LATIN") for c in tu if c.isalpha()
    )


def tach_tu(value: str | None, ung_vien: bool = False) -> set[str]:
    """Tập các từ có nghĩa trong một chuỗi.

    HAI ĐƯỜNG, vì hai loại chữ cần hai cách đối xử trái ngược:

    * Chữ KHÔNG dấu hoặc phi-Latinh (tiếng Anh, Hindi, Thái, Hán): bỏ dấu rồi tách,
      y như bản gốc. `fold_text` phiên âm Devanagari/Thái/Ả Rập sang Latinh nên
      `फल` và `फल थोक विक्रेता` ra cùng một gốc.

    * Chữ Latinh CÓ DẤU (tiếng Việt): GIỮ NGUYÊN DẤU. Bỏ dấu ở đây gây hai lỗi
      đối xứng nhau, đều đã đo trên dữ liệu thật của job 3:

        - KHỚP NHẦM vì hai từ khác nghĩa ra cùng một chuỗi: "xây dựng" và
          "gia dụng" cùng thành `dung`, nên một cửa hàng vật liệu xây dựng
          (Showroom Gạch ốp lát) được giữ cho từ khoá "gia dụng thông minh" và
          "túi xách phụ kiện".
        - MẤT HẾT từ có nghĩa: `quần` thành `quan` (trùng với "quán", đã nằm
          trong danh sách từ vô nghĩa), `áo`/`mẹ`/`bé` ngắn hơn ngưỡng 3 ký tự.
          Từ khoá "Shop mẹ và bé" ra TẬP RỖNG, nhãn "Cửa hàng quần áo nữ" cũng rỗng
          nên bị coi là nhãn vô nghĩa — mọi cửa hàng quần áo đều phải đưa cho AI,
          còn không có AI thì được giữ hết không lọc gì.

    `ung_vien` phân biệt hai phía của phép so, vì dấu chỉ nên "nới" theo MỘT chiều:
    nhãn Google và tên doanh nghiệp (ứng viên) hay viết không dấu, còn từ khoá
    người dùng gõ có dấu thì chỉ cần khớp đúng dấu. Phía từ khoá đánh thêm dấu
    `~` để so với ứng viên không dấu mà KHÔNG khớp nhầm với ứng viên có dấu khác.
    """
    if not value:
        return set()
    ra: set[str] = set()
    for tho in _TACH.split(unicodedata.normalize("NFC", value).lower()):
        if not tho:
            continue
        if _la_latinh_co_dau(tho):
            if tho in VO_NGHIA_DAU or len(tho) < 2:
                continue
            thuong = "".join(c for c in fold_text(tho) if c.isalnum())
            ra.add(tho)
            # Ứng viên có dấu cũng được so với từ khoá KHÔNG dấu (người dùng gõ
            # "quan ao"); từ khoá có dấu thì chỉ so với ứng viên không dấu.
            ra.add(thuong if ung_vien else "~" + thuong)
            continue
        # Đường cũ, giữ nguyên hành vi.
        folded = fold_text(tho)
        dem: list[str] = []
        manh: list[str] = []
        for ch in folded + " ":
            if ch.isalnum():
                dem.append(ch)
            elif dem:
                manh.append("".join(dem))
                dem = []
        for t in manh:
            if len(t) > 2 and t not in VO_NGHIA:
                g = _goc(t)
                ra.add(g)
                if ung_vien:
                    ra.add("~" + g)
    return ra


def _goc(tu: str) -> str:
    """Gộp số nhiều về số ít bằng cách bỏ đuôi "s".

    Không phải chuyện cho đẹp: Google gắn CẢ HAI nhãn "Fruit wholesaler" và
    "Fruits wholesaler" cho cùng một loại cửa hàng, và tên doanh nghiệp thì hầu
    hết viết số nhiều ("Rahim Fruits Stall", "Md Israil Fruits Cold Stores").
    Không gộp thì danh mục ghi số ít sẽ vứt oan tất cả những chỗ viết số nhiều.

    Ngưỡng 4 ký tự để không cắt nhầm những từ vốn kết thúc bằng "s". Cắt cả hai
    vế như nhau nên kể cả cắt sai thì hai bên vẫn sai giống nhau và vẫn khớp.
    """
    return tu[:-1] if len(tu) > 3 and tu.endswith("s") else tu


# Nhãn ngành nghề KHÔNG nói lên điều gì về mặt hàng.
#
# Rơi vào đây thì luật cứng bó tay — dù có khớp hay không khớp danh mục cũng
# không kết luận được, vì bản thân cái nhãn chẳng mang thông tin. Danh sách cố ý
# rất ngắn: mỗi mục thêm vào là một loại nhãn bị đẩy sang cho AI xử, tốn token
# và chậm hơn.
NHAN_VO_NGHIA = frozenset({"corporate office", "establishment", "permanently closed"})


def nhan_chung_chung(category: str | None) -> bool:
    """Nhãn này có vô nghĩa với việc phân biệt ngành hàng không.

    Hai cách để vô nghĩa, và cách đầu bắt được nhiều hơn hẳn: sau khi bỏ hết từ
    có mặt ở mọi tên ngành ("store", "shop", "company", "general"...) thì không
    còn chữ nào. "General store" -> rỗng. "Company" -> rỗng. "Bakery" -> {bakery},
    có nghĩa hẳn hoi nên không vào đây.
    """
    if not (category or "").strip():
        return False
    if fold_text(category) in NHAN_VO_NGHIA:
        return True
    return not tach_tu(category)


# Nhãn nằm trong CHUỖI CUNG ỨNG THỰC PHẨM nhưng không có trong danh mục.
#
# Đây là lưới an toàn cho chỗ yếu nhất của cả cơ chế: danh mục ngành nghề do AI
# sinh, mỗi lần một khác, và người dùng KHÔNG sửa được nó (cố ý — họ chỉ muốn gõ
# từ khoá rồi nhận dữ liệu sạch). AI quên một nhãn là cả một loại doanh nghiệp
# biến mất, không ai biết, không có đường vá.
#
# Đo trên 174 địa điểm Andaman với một danh mục 12 nhãn thật do AI sinh: luật
# cứng loại 68 dòng, trong đó 6 dòng đáng tiếc — 3 "Supermarket", 1 "Food
# products supplier", 1 "Food Processing Company", 1 "Packaging company" (đóng
# gói trái cây). Thêm lưới này thì cả 6 được đưa cho AI xem lại, và chỉ tốn thêm
# 7 lượt (10% -> 13% số thẻ phải hỏi AI).
#
# CỐ Ý KHÔNG liệt kê những từ thương mại chung ("store", "shop", "company",
# "supplier", "enterprise"): thử thì nó kéo theo 30 dòng, gần hết là "Pet store",
# "Tile store", "Men's clothing store", "Computer store" — gấp bốn lần chi phí
# để cứu đúng chừng ấy dòng.
#
# Danh sách này gắn với NGÀNH HÀNG của dự án (nông sản, thực phẩm). Đổi sang
# ngành khác thì phải sửa nó, và sửa sót sẽ hỏng âm thầm — nên nó nằm đây, cạnh
# chỗ dùng, chứ không giấu trong cấu hình.
CHUOI_CUNG_UNG = (
    "food", "produce", "agri", "agro", "farm", "grocer", "supermarket", "hypermarket",
    "wholesal", "import", "export", "distribut", "trading", "packag", "market",
    "thuc pham", "nong san", "trai cay", "hoa qua", "rau", "cho", "sieu thi",
    "ban buon", "ban si", "xuat khau", "nhap khau", "phan phoi", "dong goi",
)


def trong_chuoi_cung_ung(*phan: str | None) -> bool:
    """Chuỗi nào trong số này có dính tới chuỗi cung ứng thực phẩm không.

    Nhận NHIỀU chuỗi vì phải soi cả TÊN chứ không riêng nhãn ngành nghề. Đo trên
    dữ liệu thật: `Reap Agro Products` bị Google gắn nhãn "Manufacturer" — nhãn
    rõ nghĩa, rõ ràng không dính ngành, nên luật loại thẳng và AI không hề được
    xem. Nhưng tên ghi "Agro Products", tức là doanh nghiệp nông sản thật.

    So trên chuỗi đã bỏ dấu chứ không so theo từ: "Vegetable wholesale market"
    và "Wholesaler" đều phải bắt được từ cùng một gốc "wholesal".
    """
    return any(tu in fold_text(x) for x in phan if x for tu in CHUOI_CUNG_UNG)


def _bang_chung_manh(chung: set[str]) -> bool:
    """Phần chung giữa nhãn và danh mục có đủ mạnh để tự quyết không.

    Mạnh khi có MỘT chữ từ 3 ký tự trở lên, hoặc từ HAI chữ khác nhau trở lên.
    Dấu `~` chỉ là cách đánh dấu phía so không dấu, bỏ đi trước khi đếm — nếu
    không thì "mỹ" và "~my" bị đếm thành hai chữ.
    """
    goc = {t.lstrip("~") for t in chung}
    return len(goc) >= 2 or any(len(t) >= 3 for t in goc)


def cham(
    category: str | None,
    name: str | None,
    cho_phep: list[str] | None,
) -> str | None:
    """Trả `LIEN_QUAN` | `RANH_GIOI` | `NGHI_RAC` | None (chưa đủ căn cứ).

    `cho_phep` gộp hai nguồn, cố ý bổ sung cho nhau:
      * tên ngành nghề do AI liệt kê cho đúng quốc gia đó — bắt được những ngành
        đúng nhưng không chứa chữ "trái cây" nào, như "किराना दुकान" (tạp hoá);
      * chính các từ khoá của job — luôn có, kể cả khi không gọi AI (Việt Nam),
        và bắt được ngành mà AI quên liệt kê.

    Trả None khi KHÔNG chấm được, và None không bao giờ bị ẩn khỏi bảng:
      * `cho_phep` rỗng   -> job này không có căn cứ nào, đừng bịa ra.
      * chưa có `category` -> Google chưa gắn nhãn; đoán bừa từ mỗi cái tên sẽ
        vứt nhầm doanh nghiệp đặt tên trung tính ("Công ty TNHH Minh Phát").
    """
    moc: set[str] = set()
    for muc in cho_phep or []:
        moc |= tach_tu(muc)
    if not moc:
        return None
    if not (category or "").strip():
        return None

    chung = tach_tu(category, ung_vien=True) & moc
    if chung:
        if _bang_chung_manh(chung):
            # Nhãn ngành nghề khớp danh mục bằng chứng đủ mạnh — không cần hỏi
            # ai thêm.
            return LIEN_QUAN
        # Khớp bằng ĐÚNG MỘT chữ ngắn ("mỹ", "áo", "bé"): quá yếu để tự quyết.
        # Đo trên dữ liệu thật: "Thẩm mỹ viện" khớp từ khoá "mỹ phẩm" chỉ vì chung
        # chữ "mỹ" — một thẩm mỹ viện không phải cửa hàng mỹ phẩm, và AI cũng
        # loại. Chữ 2 ký tự có dấu là thứ mới được tính (trước đây bị bỏ hết),
        # nên phải có thêm lớp chặn này; đẩy sang AI chứ không loại, vì "Cửa hàng
        # áo thun" cũng khớp "quần áo" bằng đúng một chữ ngắn mà là lead thật.
        return RANH_GIOI
    if nhan_chung_chung(category):
        # Nhãn không nói gì về mặt hàng. Loại thẳng là vứt oan
        # `Battambang Agro Industry Co., Ltd.` (nhãn "Company"); giữ thẳng là
        # mời mọi "General store" vào bảng. Cả hai đều sai — phải nhìn kỹ hơn.
        return RANH_GIOI
    if tach_tu(name, ung_vien=True) & moc:
        # Nhãn ngành RÕ RÀNG là thứ khác, chỉ có mỗi cái tên dính. Đủ để nghi,
        # không đủ để kết luận: "Rahim Fruits Stall" (nhãn "Cửa hàng") là thật,
        # còn "Dress store"/"Book store" thì không.
        return RANH_GIOI
    if trong_chuoi_cung_ung(category, name):
        # Nhãn hoặc TÊN vẫn nằm trong chuỗi cung ứng thực phẩm dù không khớp
        # danh mục. Gần như chắc chắn là danh mục thiếu chứ không phải doanh
        # nghiệp lạc đề — "Supermarket", "Food Processing Company", hoặc
        # `Reap Agro Products` bị Google gắn nhãn "Manufacturer".
        return RANH_GIOI
    # Nhãn rõ ràng, rõ ràng không dính gì tới ngành, tên cũng không. Chắc chắn
    # nhất trong ba ca — "Bakery", "Travel agency", "Pharmacy" nằm ở đây.
    return NGHI_RAC


# Thứ tự từ chắc chắn nhất tới yếu nhất. Dùng cho `gop` để "chỉ nâng, không hạ"
# vẫn đúng khi có ba mức chứ không phải hai.
THU_TU = {LIEN_QUAN: 3, RANH_GIOI: 2, NGHI_RAC: 1}


def gop(cu: str | None, moi: str | None) -> str | None:
    """Điểm mới đè lên điểm cũ như thế nào khi một địa điểm được tìm lại.

    Luật: CHỈ ĐƯỢC NÂNG, KHÔNG ĐƯỢC HẠ. Một địa điểm có thể lọt vào nhiều job với
    nhiều bộ từ khoá khác nhau; nếu job sau hạ nó xuống `NGHI_RAC` thì một lead
    đã được xác nhận đúng ngành sẽ tự biến mất khỏi bảng mà không ai biết.
    Nhầm về phía HIỆN THỪA thì còn nhìn thấy mà sửa, nhầm về phía ẩn mất thì không.
    """
    if moi is None:
        return cu
    if cu is None:
        return moi
    return max(cu, moi, key=lambda x: THU_TU.get(x, 0))
