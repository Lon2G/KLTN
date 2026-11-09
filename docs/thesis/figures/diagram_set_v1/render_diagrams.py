"""Vietnamese thesis diagrams, explicitly laid out from verified project sources.

Run with the project's .venv Python. Only writes assets in this directory.
No model inference, experiment execution, raw-data mutation or network access.
The Python definitions are the layout/content source of truth; Mermaid is an
editable semantic alternative, not a promise of identical automatic layout.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "kltn-diagrams-mpl"))
os.environ.setdefault("XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "kltn-diagrams-cache"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, findfont
from matplotlib.ft2font import FT2Font
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from PIL import Image, ImageOps, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
AS_OF = "2026-11-09"
INK = "#20384A"
COLORS = {
    "done": ("#F1F6FA", "#284E68"),
    "pending": ("#FFF7E8", "#946425"),
    "note": ("#F5F5F3", "#656C71"),
    "alert": ("#FBF0EF", "#98524B"),
    "output": ("#EFF6F2", "#396852"),
}
FONT_DIR = Path("/System/Library/Fonts/Supplemental")


def font(bold=False):
    p = FONT_DIR / ("Times New Roman Bold.ttf" if bold else "Times New Roman.ttf")
    return FontProperties(fname=str(p if p.exists() else findfont(
        FontProperties(family="DejaVu Serif", weight="bold" if bold else "normal"))))


def read_summary(name):
    return json.loads((ROOT / "data/experiments" / name / "summary.json").read_text())


class Diagram:
    def __init__(self, stem, title, chapter, height, subtitle, caption, sources, width=100,
                 footer=(), legend=False):
        self.stem, self.title, self.chapter = stem, title, chapter
        self.width, self.height = width, height
        self.subtitle, self.caption = subtitle, caption
        self.sources, self.footer, self.legend = sources, list(footer), legend
        self.nodes, self.edges, self.labels = [], [], []

    def box(self, key, x, y, w, h, title, body="", kind="done", fs=12.5, hs=13.5):
        self.nodes.append(dict(id=key, x=x, y=y, w=w, h=h, title=title, body=body,
                               kind=kind, fs=fs, hs=hs))
        return key

    def anchor(self, key, side, fraction=.5):
        n = next(n for n in self.nodes if n["id"] == key)
        x, y, w, h = (n[k] for k in ("x", "y", "w", "h"))
        return {"T": (x+w*fraction, y), "B": (x+w*fraction, y+h),
                "L": (x, y+h*fraction), "R": (x+w, y+h*fraction)}[side]

    def edge(self, start, end, points=None, label="", at=None, pending=False,
             start_side="B", end_side="T", start_f=.5, end_f=.5, fs=10.8):
        if points is None:
            points = [self.anchor(start, start_side, start_f), self.anchor(end, end_side, end_f)]
        self.edges.append(dict(start=start, end=end, points=points, label=label,
                               at=at, pending=pending, fs=fs))

    def label(self, x, y, text, fs=11.3, bold=False):
        self.labels.append(dict(x=x, y=y, text=text, fs=fs, bold=bold))


def definitions():
    ds = []
    clean = "data/processed/olist_clean_v1/README.md"
    temporal = "data/experiments/olist_temporal_evaluation_plan_v1/summary.json"
    product = "scripts/03_anomaly_detection/build_product_stage_scores.py"
    score_doc = "docs/product_stage_scores.md"
    test = "data/experiments/olist_selected_holdout_v1/summary.json"
    replay_doc = "docs/event_stage_scoring.md"
    replay_summary = "data/experiments/olist_event_stage_scoring_v1/summary.json"
    coord = "scripts/05_multi_agent/coordinator.py"
    review = "scripts/06_agent_evaluation/review_v2/runner.py"
    pilot = "data/experiments/olist_agent_live_review_v2_case2/summary.json"
    comparison = "templates/agent_comparison_v1/protocol.json"
    readiness = "docs/thesis_readiness_2026-11-07.md"

    d = Diagram("01_research_overview.vi", "QUY TRÌNH NGHIÊN CỨU", 1, 139,
        "Olist / bed_bath_table · Phạm vi và hiện trạng nghiên cứu",
        "Quy trình nghiên cứu của đề tài, phân biệt kết quả đã triển khai và phần dự kiến.",
        [clean, temporal, test, pilot, comparison, readiness], legend=True,
        footer=["Nét liền: đã có triển khai/kết quả, không đồng nghĩa đã hoàn thiện.",
                "Phát hiện dấu hiệu bất thường; chưa xác nhận nguyên nhân tắc nghẽn."])
    d.box("question", 12,14,76,11, "Vấn đề và câu hỏi nghiên cứu", "Thời gian xử lý bất thường và điểm nghẽn tiềm năng")
    d.box("data", 12,31,76,12, "Chuẩn bị dữ liệu và phân tích khám phá", "Olist thật → rà soát chất lượng → chuẩn hóa → EDA")
    d.box("split", 12,49,76,12, "Thiết kế thực nghiệm theo thời gian", "Train: fit/tham chiếu · Validation: lựa chọn · Test: đánh giá")
    d.box("detector", 4,68,44,18, "Phát hiện theo công đoạn", "Nhóm tham chiếu có bối cảnh\nSo sánh các phương pháp\nCố định ứng viên")
    d.box("agents", 52,68,44,18, "Giải thích bằng ba agent", "Quy trình → bối cảnh → kiểm chứng\nCông cụ tính toán có nguồn\nĐã thử nghiệm nhóm nhỏ")
    d.box("evaluation", 12,93,76,12, "Đánh giá kết quả đã có", "Độ bao phủ, ổn định, đối chiếu trễ hẹn; pilot chất lượng giải thích")
    d.box("planned", 12,112,76,12, "Hoàn thiện thực nghiệm và trình bày — dự kiến", "Đối chứng AI đầy đủ · chấm định tính · BI local · tổng hợp kết luận", "pending")
    for a,b in [("question","data"),("data","split")]: d.edge(a,b)
    for b in ["detector","agents"]:
        x = 26 if b=="detector" else 74
        d.edge("split",b,[(50,61),(50,64.5),(x,64.5),(x,68)])
        d.edge(b,"evaluation",[(x,86),(x,89.5),(50,89.5),(50,93)])
    d.edge("detector","agents",start_side="R",end_side="L")
    d.edge("evaluation","planned",pending=True)
    ds.append(d)

    d = Diagram("02_conceptual_framework.vi", "KHUNG KHÁI NIỆM CỦA NGHIÊN CỨU", 2, 131,
        "Quan hệ giữa các thành phần · Không phải sơ đồ tuần tự thực thi",
        "Khung khái niệm liên kết biểu diễn quy trình, phát hiện bất thường, Multi-Agent và BI.",
        [clean, score_doc, coord, review, readiness],
        footer=["Tác giả tổng hợp theo thiết kế đề tài; hình không thay thế trích dẫn lý thuyết.",
                "Nét đứt: lớp BI dự kiến; quan hệ trong hình không chứng minh quan hệ nhân quả."])
    d.box("events",12,14,76,12,"Đơn hàng dưới dạng chuỗi sự kiện", "Mã đơn (case_id) · hoạt động · thời điểm ghi nhận")
    d.box("process",4,35,44,20,"Biểu diễn và phân tích quy trình", "Bốn mốc → ba công đoạn\nThời lượng và thứ tự sự kiện\nMốc thiếu hoặc mâu thuẫn")
    d.box("anomaly",52,35,44,20,"Phát hiện thời gian bất thường", "So sánh nhóm tham chiếu\nChênh lệch và mức cảnh báo\nĐộ bao phủ và bất định")
    d.box("evidence",12,64,76,12,"Bằng chứng có thể truy xuất và tính lại", "Mốc gốc · công thức · nhóm tham chiếu · điều kiện và giới hạn")
    d.box("agents",12,84,76,13,"Multi-Agent hỗ trợ diễn giải", "Phân tích quy trình → đối chiếu bối cảnh → kiểm chứng nhận định")
    d.box("bi",12,105,76,12,"Business Intelligence — thiết kế dự kiến", "Tổng hợp nhóm cần chú ý → xem đơn → xem bằng chứng", "pending")
    for b,x in [("process",26),("anomaly",74)]:
        d.edge("events",b,[(50,26),(50,30),(x,30),(x,35)])
        d.edge(b,"evidence",[(x,55),(x,59),(50,59),(50,64)])
    d.edge("process","anomaly",start_side="R",end_side="L")
    d.edge("evidence","agents")
    d.edge("agents","bi",pending=True)
    ds.append(d)

    # Cardinalities checked on data/raw on 2026-11-09: customer_id is unique per order
    # (1–1); customer_unique_id has up to 17 orders; up to 29 payment rows per order;
    # up to 3 reviews per order and a review_id can appear on up to 3 orders.
    d = Diagram("03_olist_data_relationships.vi", "QUAN HỆ DỮ LIỆU OLIST",3,155,
        "Sơ đồ quan hệ phục vụ phân tích · Hiển thị các khóa liên kết chính",
        "Quan hệ giữa chín bảng nguồn Olist và lớp tham chiếu địa lý dẫn xuất.",
        [clean,"data/processed/olist_clean_v1/manifest.json","docs/product_stage_scores.md"],
        footer=["1 – n: một bản ghi bảng cha ứng với nhiều dòng bảng con; tổng hợp đúng cấp trước khi ghép.",
                "1 – n*: review_id không duy nhất; một đánh giá có thể gắn với nhiều đơn.",
                "customer_id là 1 – 1 với đơn; customer_unique_id nhận diện khách qua nhiều đơn.",
                "ZIP không duy nhất trong geolocation; không ghép trực tiếp bảng này vào đơn hàng."])
    d.box("customers",3,14,27,18,"Khách hàng\ncustomers","customer_id\ncustomer_unique_id",fs=11.4,hs=12.5)
    d.box("orders",37,14,26,18,"Đơn hàng\norders","order_id · customer_id\n4 mốc + ngày dự kiến",fs=11.4,hs=12.5)
    d.box("reviews",70,14,27,18,"Đánh giá\norder_reviews","review_id · order_id\nĐiểm và bình luận",fs=11.4,hs=12.5)
    d.box("products",3,54,27,18,"Sản phẩm\nproducts","product_id\nproduct_category_name",fs=11.4,hs=12.5)
    d.box("items",37,54,26,18,"Dòng hàng\norder_items","order_id · order_item_id\nproduct_id · seller_id",fs=11,hs=12.5)
    d.box("payments",70,54,27,18,"Thanh toán\norder_payments","order_id\nCác thuộc tính thanh toán",fs=11.4,hs=12.5)
    d.box("translation",3,94,27,18,"Tên ngành hàng\ntranslation","Tên gốc → tên tiếng Anh",fs=11.4,hs=12.5)
    d.box("sellers",37,94,26,18,"Người bán\nsellers","seller_id\nseller_zip_code_prefix",fs=11.4,hs=12.5)
    d.box("geo",70,94,27,18,"Tọa độ nguồn\ngeolocation","ZIP prefix · vĩ độ · kinh độ\nNhiều quan sát / ZIP",fs=11.1,hs=12.5)
    d.box("zip",3,122,94,12,"Tham chiếu địa lý dẫn xuất và kiểm tra chất lượng", "Ghép theo ZIP của khách/người bán; giữ trạng thái không đủ điều kiện", "output")
    d.edge("customers","orders",start_side="R",end_side="L",label="1 – 1",at=(33.5,20.3),fs=10.4)
    d.edge("orders","reviews",start_side="R",end_side="L",label="1 – n*",at=(66.5,20.3),fs=10.4)
    d.edge("orders","items",label="1 – n",at=(50,43))
    d.edge("orders","payments",[(60,32),(60,43),(83.5,43),(83.5,54)],label="1 – n",at=(72,43))
    d.edge("products","items",start_side="R",end_side="L",label="1 – n",at=(33.5,60.3),fs=10.4)
    d.edge("sellers","items",start_side="T",end_side="B",label="seller_id · 1 – n",at=(50,83))
    d.edge("translation","products",start_side="T",end_side="B",label="Tên ngành gốc · 1 – n",at=(16.5,83))
    d.edge("geo","zip",[(83.5,112),(83.5,122)])
    d.edge("zip","sellers",[(50,122),(50,112)])
    d.edge("zip","customers",[(13,122),(13,117),(1,117),(1,23),(3,23)])
    ds.append(d)

    d = Diagram("04_order_milestones_and_stages.vi", "BỐN MỐC SỰ KIỆN VÀ BA CÔNG ĐOẠN",3,144,
        "Thời lượng tính từ mốc quan sát thực tế ở cấp đơn hàng",
        "Ánh xạ bốn mốc Olist thành ba công đoạn; ngày giao dự kiến được đối chiếu độc lập.",
        [clean,product,score_doc],
        footer=["Khoảng duyệt–bàn giao không đồng nghĩa toàn bộ thời gian đóng gói.",
                "Thiếu hoặc mâu thuẫn mốc: giữ bằng chứng gốc, không điền thời gian giả."])
    milestones=[("purchase",14,"Đặt hàng","order_purchase_timestamp"),
        ("approval",40,"Duyệt đơn","order_approved_at"),
        ("handoff",66,"Bàn giao cho vận chuyển","order_delivered_carrier_date"),
        ("delivery",92,"Giao cho khách","order_delivered_customer_date")]
    for k,y,t,b in milestones:d.box(k,4,y,60,11,t,b,fs=12)
    for a,b,y,text in [("purchase","approval",33,"Xử lý đơn hàng\nΔt1 = duyệt − đặt"),
                        ("approval","handoff",59,"Chuẩn bị và bàn giao\nΔt2 = bàn giao − duyệt"),
                        ("handoff","delivery",85,"Vận chuyển và giao hàng\nΔt3 = giao − bàn giao")]:
        d.edge(a,b,points=[d.anchor(a,"B",.10),d.anchor(b,"T",.10)],label=text,at=(39,y),fs=12.5)
    d.box("promise",70,81,27,19,"Ngày giao\ndự kiến","Trường riêng\nkhông là sự kiện", "note",fs=12,hs=13)
    d.box("calendar",70,110,27,18,"Đối chiếu ngày","Sớm / đúng / trễ\nhoặc chưa xác định", "output",fs=12,hs=13)
    d.box("basis",4,110,60,18,"Cơ sở ngày hẹn: chưa xác minh", "Không suy diễn ngày hẹn ngẫu nhiên\nhoặc được tính từ lịch sử khi chưa có chứng cứ", "note")
    d.edge("promise","calendar")
    d.edge("delivery","calendar",[(64,97.5),(67,97.5),(67,119),(70,119)])
    ds.append(d)

    split=read_summary("olist_temporal_evaluation_plan_v1")
    # The 80/20 proposal in this artifact was never activated. Use its explicitly
    # recorded existing split, not proposed_development_orders/proposed_test_orders.
    assert split["cohort_orders"] == 9024
    assert split["old_split_counts"] == {"train":5480,"validation":1853,"test":1691}
    assert read_summary("olist_selected_holdout_v1")["total_test_orders"] == 1691
    d = Diagram("05_data_preparation_and_split.vi", "CHUẨN BỊ DỮ LIỆU VÀ CHIA TẬP",3,153,
        "Giữ dữ liệu gốc · Lưu vết xử lý · Xác định điều kiện theo từng phép phân tích",
        "Luồng chuẩn hóa, kiểm tra chất lượng, chọn phạm vi ngành hàng và phân chia theo thời gian.",
        [clean,temporal,"scripts/01_data_preparation/clean_olist_data.py",test],
        footer=["Số đơn ở trên là phạm vi chia tập; số đủ điều kiện được báo riêng cho từng phép đánh giá.",
                "Chỉ fit trên train; test đã được xem kết quả, không còn là tập kiểm chứng chưa từng quan sát.",
                "Chia thời gian không tự bảo đảm hết rò rỉ: thời điểm có sẵn của bối cảnh vẫn cần kiểm soát."])
    d.box("raw",12,14,76,10,"Chín bảng dữ liệu Olist gốc", "Giữ nguyên dữ liệu nguồn và dấu vết phiên bản")
    d.box("normalize",12,30,76,13,"Chuẩn hóa cấu trúc", "Kiểu dữ liệu · chuỗi/ID · ZIP · trường thời gian\nKhông tự điền mốc, giá trị tiền hoặc tọa độ bị thiếu")
    d.box("quality",12,49,76,13,"Rà soát và ghi nhận chất lượng", "Khóa/liên kết · thiếu dữ liệu · thứ tự thời gian\nChỉ loại quan sát địa lý trùng hoàn toàn theo chính sách đã lưu")
    d.box("snapshot",4,69,44,15,"Snapshot chuẩn hóa + EDA", "Giữ toàn bộ đơn nguồn\nLập đặc trưng và điều kiện sử dụng")
    d.box("audit",52,69,44,15,"Hồ sơ đối chiếu", "Vị trí bản ghi · lý do · hành động\nHash nguồn và kết quả", "note")
    d.box("cohort",4,92,92,11,"Phạm vi bed_bath_table trong thời kỳ nghiên cứu", "9.024 đơn · tách theo thời điểm đặt hàng, không xáo trộn ngẫu nhiên")
    d.box("train",4,112,28,21,"Train · 5.480 đơn", "03/2017–02/2018\nFit mô hình / tham chiếu\nLịch sử đủ điều kiện",fs=11.9,hs=12.5)
    d.box("validation",36,112,28,21,"Validation · 1.853 đơn", "03/2018–05/2018\nSo sánh và lựa chọn\nCố định cấu hình",fs=11.9,hs=12.5)
    d.box("test",68,112,28,21,"Test · 1.691 đơn", "06/2018–08/2018\nĐánh giá cấu hình cố định\nKhông tinh chỉnh trên test",fs=11.9,hs=12.5)
    d.edge("raw","normalize");d.edge("normalize","quality")
    for b,x in [("snapshot",26),("audit",74)]:d.edge("quality",b,[(50,62),(50,65.5),(x,65.5),(x,69)])
    d.edge("snapshot","cohort",[(26,84),(26,92)])
    for b,x in [("train",18),("validation",50),("test",82)]:d.edge("cohort",b,[(50,103),(50,107),(x,107),(x,112)])
    ds.append(d)

    d = Diagram("06_reference_and_warning.vi", "THAM CHIẾU VÀ CẢNH BÁO CÔNG ĐOẠN",3,161,
        "Ứng viên cố định: product_context_stage_score_gt0 · Điểm không phải xác suất",
        "Lựa chọn nhóm tham chiếu, tính chênh lệch và điểm; tách biệt điều kiện đánh giá với trễ hẹn.",
        [product,score_doc,"scripts/03_anomaly_detection/apply_frozen_stage_reference.py"],
        footer=["d: thời lượng; μ: trung bình tham chiếu; F(d): tỷ lệ mẫu tham chiếu ≤ d.",
                "n ≥ 30 là quy ước hỗ trợ tối thiểu; không chứng minh độ chính xác hay đủ kiểm soát bối cảnh.",
                "Giao sớm không xóa cảnh báo công đoạn; điểm thiếu không được thay bằng 0."])
    d.box("input",4,14,57,10,"Mốc và bối cảnh của đơn", "Lịch sử train hợp lệ; loại đơn đang xét khỏi tham chiếu")
    d.box("eligible",4,30,57,13,"Kiểm tra điều kiện đánh giá", "Mốc quan sát, thời lượng và chất lượng bối cảnh\nPhân biệt thiếu mốc với mốc mâu thuẫn")
    d.box("unavailable",68,30,28,20,"Chưa đánh giá được", "Thiếu mốc / mâu thuẫn\nThiếu mẫu tham chiếu\nĐiều kiện khác không đạt", "alert",fs=11.8,hs=12.5)
    d.box("product",4,50,57,16,"1. Nhóm theo sản phẩm và bối cảnh", "Đơn một sản phẩm/một seller; n ≥ 30\nĐạt nhóm: chuyển sang tính điểm\nKhông đạt: xét nhóm dự phòng",fs=12)
    d.box("fallback",4,72,57,14,"2. Nhóm dự phòng cấp ngành", "Nhóm bối cảnh có địa lý đạt điều kiện; n ≥ 30\nCông khai các điều kiện đã được nới lỏng")
    d.box("score",4,96,57,26,"Tính chênh lệch và điểm riêng biệt", "Δ = d − μ; Δ < 0: nhanh hơn tham chiếu\nNếu d ≤ μ hoặc d ≤ 0: điểm = 0\nCòn lại: 100 × clip((F(d) − 0,95) / 0,05; 0; 1)\nCờ công đoạn kéo dài khi điểm > 0",fs=11.5,hs=13)
    d.box("missing",68,96,28,26,"Không có điểm", "Giữ giá trị thiếu (NULL)\nGhi rõ lý do và phạm vi\nKhông gán bình thường\ncho phần chưa đánh giá", "alert",fs=11.8,hs=12.5)
    d.box("output",4,131,92,13,"Đầu ra gồm các chiều thông tin độc lập", "Khả năng đánh giá + kết quả từng công đoạn + trạng thái giao so với ngày hẹn\nĐiểm đơn = max điểm công đoạn có sẵn; luôn kèm độ bao phủ", "output",fs=12)
    d.edge("input","eligible");d.edge("eligible","product",label="Đạt",at=(22,46.5))
    d.edge("eligible","unavailable",[(61,36.5),(68,36.5)],label="Không\nđạt",at=(64.5,32.6),fs=10.2)
    d.edge("product","fallback",label="Không đủ nhóm sản phẩm",at=(46,69))
    d.edge("product","score",[(12,66),(12,68),(1.5,68),(1.5,109),(4,109)],label="Đạt nhóm\nsản phẩm",at=(9.5,91),fs=10.4)
    d.edge("fallback","score",label="Đạt",at=(36,91))
    d.edge("fallback","unavailable",[(61,79),(82,79),(82,50)],label="Không đạt",at=(82,64),fs=10.8)
    d.edge("unavailable","missing",[(96,40),(98.5,40),(98.5,109),(96,109)])
    d.edge("score","output",[(32.5,122),(32.5,131)])
    d.edge("missing","output",[(82,122),(82,131)])
    ds.append(d)

    d = Diagram("07_system_architecture.vi", "KIẾN TRÚC HỆ THỐNG PHÂN TÍCH",4,150,
        "Phân biệt lớp tính toán cố định, lớp giải thích và lớp trình bày",
        "Kiến trúc các thành phần đã triển khai và lớp BI local dự kiến.",
        [clean,replay_doc,coord,"scripts/05_multi_agent/evidence_tools.py",review,readiness],
        footer=["Bộ điều phối là code, không phải agent thứ tư; AI không sửa điểm và ngưỡng detector.",
                "Đã thử từng phần và pilot; chưa chứng minh luồng đầu-cuối near real-time trong sản xuất."])
    d.box("data",4,14,42,16,"Dữ liệu chuẩn hóa", "Bảng nguồn · nhật ký sự kiện\nBối cảnh và cờ chất lượng")
    d.box("training",54,14,42,16,"Phát triển và chọn phương pháp", "Fit trên train · chọn trên validation\nĐánh giá cấu hình cố định")
    d.box("replay",4,40,42,18,"Phát lại và chấm công đoạn", "Trạng thái theo thời điểm sự kiện\nÁp dụng detector đã cố định\nLưu trạng thái và lịch sử thay đổi")
    d.box("frozen",54,40,42,18,"Tài nguyên đã cố định", "Nhóm tham chiếu · công thức\nĐiều kiện đánh giá · cấu hình\nHash và phiên bản")
    d.box("evidence",4,68,92,14,"Lớp công cụ và bằng chứng có nguồn", "Trạng thái quan sát · tính lại số liệu · kiểm tra thời gian · đối chiếu nhóm tương đồng")
    d.box("agents",4,92,57,19,"Bộ điều phối và ba agent", "Process Analyst → Context Analyst → Evidence Verifier\nKiểm tra bằng code và sửa có giới hạn",fs=11.7)
    d.box("budget",68,92,28,19,"Ràng buộc vận hành", "Token / lượt / ngân sách\nNhật ký API và lỗi\nBằng chứng truy xuất", "note",fs=11.6,hs=12.3)
    d.box("results",4,121,57,14,"Kết quả và dấu vết kiểm chứng", "Điểm cố định + nhận định được chấp nhận\nGiữ phần bị loại, trạng thái chưa rõ và giới hạn", "output")
    d.box("bi",68,121,28,14,"BI local — dự kiến", "Tổng quan → nhóm → đơn\n→ bằng chứng", "pending",fs=11.8,hs=12.5)
    d.edge("data","training",start_side="R",end_side="L")
    d.edge("data","replay");d.edge("training","frozen")
    d.edge("frozen","replay",start_side="L",end_side="R")
    d.edge("replay","evidence",[(25,58),(25,68)])
    d.edge("frozen","evidence",[(75,58),(75,68)])
    d.edge("evidence","agents",[(32.5,82),(32.5,92)])
    d.edge("budget","agents",start_side="L",end_side="R")
    d.edge("agents","results")
    d.edge("results","bi",start_side="R",end_side="L",pending=True)
    ds.append(d)

    # Portrait layout (A4 content width): main flow on the left, side branches on the right.
    d = Diagram("08_multi_agent_coordination.vi", "PHỐI HỢP BA AGENT VÀ KIỂM CHỨNG ĐẦU RA",4,186,
        "Bộ điều phối bằng code · Trình tự cố định · Bao gồm lớp kiểm tra bao phủ review_v2",
        "Luồng ba agent với kiểm tra bằng code, vòng sửa nhận định và vòng bổ sung cảnh báo bị bỏ sót.",
        [coord,"scripts/05_multi_agent/metadata_contracts.py",review,
         "scripts/06_agent_evaluation/review_v2/warning_coverage.py",pilot],
        footer=["Mỗi vòng sửa có giới hạn riêng, dùng chung trần lượt/token/ngân sách; không trao đổi tự do.",
                "Lỗi giải thích không xóa kết quả detector; sửa bao phủ thất bại thì giữ đầu ra trước đó."])
    main=dict(fs=12,hs=13)
    side=dict(fs=11,hs=12.3)
    d.box("contract",4,14,54,16,"Kiểm tra trước khi gọi AI", "Ràng buộc thời gian · loại đơn khỏi train\nTính lại số liệu cố định",**main)
    d.box("process",4,36,54,16,"Process Analyst", "Phân tích công đoạn\nNhận định / ghi chú / đề xuất → kiểm tra bằng code",**main)
    d.box("context",4,58,54,16,"Context Analyst", "Nhận kết quả Process; đối chiếu nhóm và giới hạn\n→ kiểm tra bằng code",**main)
    d.box("verifier",4,80,54,16,"Evidence Verifier", "Kiểm chứng từng mục: accept / revise / reject\nÁp dụng cả cho ghi chú và đề xuất",**main)
    d.box("publication",4,106,54,16,"Chấp nhận từng mục", "Code gate đạt VÀ accept · tách riêng mục bị loại\nKhông suy diễn nguyên nhân", "output",**main)
    d.box("coverage",4,132,54,16,"Kiểm tra độ bao phủ", "Giải thích đã nêu đủ công đoạn được cảnh báo?\nChỉ kiểm tra đầu ra khả dụng",**main)
    d.box("errors",68,36,28,26,"Nhánh dừng", "unavailable_budget:\nchặn do giới hạn\nunavailable_api:\nAPI không khả dụng\ninconclusive: không đủ\nkết luận / lỗi ràng buộc", "alert",**side)
    d.box("revision",68,102,28,22,"Vòng sửa nhận định", "Chỉ khi cần, tối đa 1 vòng\nAnalyst sửa → code gate\n→ Verifier chấm lại",**side)
    d.box("coverage_repair",68,132,28,22,"Bổ sung nếu bỏ sót", "Tối đa 1 lần nếu còn lượt\nProcess → code gate\n→ Verifier kiểm chứng lại",**side)
    d.box("final",4,160,92,13,"Kết quả công bố kèm bằng chứng và giới hạn", "Nhận định · ghi chú đã kiểm chứng · đề xuất kiểm tra\nphần bị loại · trạng thái bao phủ", "output",fs=12)
    for a,b in [("contract","process"),("process","context"),("context","verifier"),("publication","coverage")]:d.edge(a,b)
    d.edge("contract","errors",[(58,20),(82,20),(82,36)],label="Lỗi hoặc thiếu điều kiện",at=(82,27.5),fs=10.4)
    d.edge("verifier","errors",[(58,84),(75,84),(75,62)],label="Lỗi hoặc\nkhông đủ kết luận",at=(86,73),fs=10.4)
    d.edge("verifier","revision",[(58,91),(88,91),(88,102)],label="Cần sửa",at=(67,91),fs=10.6)
    d.edge("verifier","publication",label="Không cần sửa",at=(44,101),fs=10.6)
    d.edge("revision","publication",[(68,114),(58,114)])
    d.edge("coverage","coverage_repair",[(58,140),(68,140)],label="Thiếu",at=(63,137.3),fs=10.4)
    d.edge("coverage","final",[(31,148),(31,160)],label="Đủ / không sửa được",at=(45,154),fs=10.6)
    d.edge("coverage_repair","final",[(82,154),(82,160)])
    ds.append(d)

    r=read_summary("olist_event_stage_scoring_v1")
    # These source-backed counts are checked before publishing the diagram set.
    assert r["events"] == 7076 and r["orders"] == 1853
    assert r["scenarios"]["verified_availability_only"]["scored_stage_rows"] == 0
    assert r["scenarios"]["assumed_static_context_at_purchase"]["scored_stage_rows"] == 4865
    assert r["scenarios"]["assumed_static_context_at_purchase"]["orders_with_any_score"] == 1726
    d = Diagram("09_event_replay.vi", "PHÁT LẠI SỰ KIỆN VÀ CHẤM CÔNG ĐOẠN",4,154,
        "Phạm vi validation đã chạy: 7.076 mốc sự kiện của 1.853 đơn",
        "Phát lại sự kiện lịch sử với hai kịch bản về thời điểm có sẵn của thông tin bối cảnh.",
        [replay_doc,replay_summary,"scripts/04_streaming/score_event_stages.py"],
        footer=["Chỉ chấm công đoạn có đủ mốc đã quan sát; không cảnh báo thời lượng công đoạn chưa kết thúc.",
                "Dữ liệu mới làm lộ mâu thuẫn có thể rút điểm hiện hành; lịch sử cũ vẫn được lưu.",
                "Near real-time ở phạm vi replay; chưa kiểm chứng độ trễ tích hợp AI và dashboard."])
    d.box("events",12,14,76,11,"Đọc các mốc Olist thực tế theo thời gian", "Không tạo sự kiện, thời điểm đến hay mốc bối cảnh giả")
    d.box("dedup",12,32,76,11,"Kiểm tra sự kiện đã xử lý", "Sự kiện lặp: không phát sinh thêm kết quả; sự kiện mới: cập nhật")
    d.box("state",12,50,76,13,"Cập nhật trạng thái quan sát theo đơn", "Chỉ dùng mốc đã thấy · kiểm tra thứ tự · giữ công đoạn chưa đủ mốc\nTham chiếu train cố định trước thời kỳ replay")
    d.box("strict",4,74,44,18,"Chỉ dùng bối cảnh đã xác minh", "verified_availability_only\nChưa xác minh thời điểm bối cảnh\n→ chưa chấm được công đoạn",fs=11.8,hs=12.7)
    d.box("assumed",52,74,44,18,"Giả định bối cảnh có từ lúc đặt", "assumed_static_context_at_purchase\nGiả định khai báo, chưa xác minh\nÁp dụng kiểm tra chất lượng và mẫu",fs=11.2,hs=12.7)
    d.box("strict_result",4,101,44,13,"Kết quả kịch bản xác minh", "0 công đoạn có điểm\nKhông suy ra đơn bình thường", "output")
    d.box("assumed_result",52,101,44,13,"Kết quả kịch bản giả định", "4.865 công đoạn có điểm\n1.726 đơn có ít nhất một điểm", "output")
    d.box("storage",4,123,92,13,"Lưu trạng thái và lịch sử thay đổi theo từng kịch bản", "Sự kiện + trạng thái + điểm + nhật ký được ghi nhất quán\nĐiểm đơn đi kèm độ bao phủ; không gộp hai kịch bản", "output")
    d.edge("events","dedup");d.edge("dedup","state")
    for a,b,c,x in [("state","strict","strict_result",26),("state","assumed","assumed_result",74)]:
        d.edge(a,b,[(50,63),(50,68),(x,68),(x,74)])
        d.edge(b,c)
        d.edge(c,"storage",[(x,114),(x,123)])
    ds.append(d)

    d = Diagram("10_bi_drilldown_design.vi", "LUỒNG KHAI THÁC BI — THIẾT KẾ DỰ KIẾN",4,141,
        "Thiết kế giao diện local · Chưa phải dashboard đã triển khai hoàn chỉnh",
        "Thiết kế luồng BI từ tổng quan đến nhóm, đơn hàng và bằng chứng phục vụ kiểm tra.",
        [test,"data/experiments/olist_bottleneck_context_v1/summary.json",pilot,readiness],
        footer=["Các khối nét đứt là thiết kế dự kiến; số liệu đầu vào đã có dưới dạng bảng và hồ sơ.",
                "So sánh seller/khu vực phải kèm số đơn, mẫu số, bối cảnh và giới hạn; không quy kết nguyên nhân."])
    d.box("data",4,14,92,13,"Nguồn đầu ra hiện có", "Điểm và độ bao phủ công đoạn · trễ hẹn · nhóm tham chiếu · bằng chứng và giới hạn", "output")
    d.box("overview",4,37,92,14,"Tổng quan quản lý — dự kiến", "Bộ lọc thời gian / ngành / công đoạn · nhóm cần chú ý · số chưa đánh giá được", "pending")
    d.box("group",4,63,44,18,"Xem nhóm cần kiểm tra", "Theo công đoạn, seller, khu vực\nSố lượng / tỷ lệ và mẫu số\nPhân bố thời gian và độ bao phủ", "pending")
    d.box("order",52,63,44,18,"Xem đơn trong nhóm", "Giữ cả đơn giao sớm có cảnh báo\nTách điểm, chênh lệch và trễ hẹn\nHiển thị lý do chưa đánh giá", "pending")
    d.box("evidence",4,94,92,14,"Truy xuất bằng chứng của đơn — dự kiến", "Mốc thực tế → công đoạn → nhóm tham chiếu → công thức/điểm\nNhận định AI đã kiểm chứng; phần không rõ và phần bị loại", "pending")
    d.box("followup",4,118,92,10,"Đề xuất bước kiểm tra và hỗ trợ quyết định — dự kiến", "Gợi ý xác minh có căn cứ; không tự xác nhận nguyên nhân hoặc hiệu quả giải pháp", "pending")
    d.edge("data","overview",pending=True)
    d.edge("overview","group",[(26,51),(26,63)],pending=True)
    d.edge("group","order",start_side="R",end_side="L",pending=True)
    d.edge("order","evidence",[(74,81),(74,94)],pending=True)
    d.edge("evidence","followup",pending=True)
    ds.append(d)

    d = Diagram("11_experiment_design.vi", "THIẾT KẾ THỰC NGHIỆM VÀ ĐÁNH GIÁ",5,157,
        "Tách đánh giá detector, kiểm chứng kỹ thuật và đánh giá chất lượng giải thích",
        "Hai nhánh đánh giá phương pháp phát hiện và đóng góp của Multi-Agent, với giới hạn của từng loại bằng chứng.",
        ["data/experiments/olist_delivery_discrimination_v1/summary.json",test,comparison,
         "data/experiments/olist_agent_comparison_v1/summary.json",pilot,readiness],
        footer=["AUC/Precision/Recall/F1 hiện đối chiếu nhãn trễ hẹn, không phải tắc nghẽn được xác nhận.",
                "Kappa đo đồng thuận theo thiết kế chấm; các agent phối hợp không phải người chấm độc lập.",
                "Giữ cả lượt lỗi/thiếu; không xếp hạng phương án AI từ một pilot hoặc số liệu chưa chạy."])
    d.box("fixed",4,14,92,13,"Dữ liệu thật và điều kiện thực nghiệm được khai báo", "Cố định phạm vi, phiên bản, tiêu chí đủ điều kiện và thời điểm quan sát; lưu cấu hình/kết quả")
    d.box("detectors",4,38,44,25,"Nhánh A · Phương pháp phát hiện", "IQR / MAD / phân vị\nIsolation Forest / LOF / OC-SVM\nĐiểm theo nhóm và phương án đối chứng\nSo sánh cùng tập validation")
    d.box("agents",52,38,44,25,"Nhánh B · Cách tạo giải thích", "Quy tắc / một agent / ba agent\nCùng đơn, detector và công cụ\nKhai báo trần tài nguyên mỗi phương án\nĐã có pilot; bộ so sánh chưa đủ")
    d.box("metrics",4,74,44,23,"Đánh giá detector đã thực hiện", "Phân biệt giao trễ hẹn\nĐộ bao phủ · ổn định theo thời gian\nTest với ứng viên đã cố định\nBáo giới hạn và mẫu số")
    d.box("technical",52,74,44,15,"Kiểm chứng kỹ thuật — pilot", "Khớp nguồn · bao phủ cảnh báo\nThời gian · token · chi phí")
    d.box("limits",4,106,44,15,"Giới hạn diễn giải", "Không lấy cờ detector làm nhãn đúng\nChưa xác nhận nguyên nhân nghẽn", "note")
    d.box("ratings",52,98,44,23,"Đánh giá định tính — dự kiến", "Chấm độc lập theo tiêu chí\nBằng chứng và tính phù hợp đề xuất\nChọn Kappa theo số người/thang chấm\nChưa có kết quả đồng thuận", "pending",fs=12)
    d.box("synthesis",4,130,92,12,"Tổng hợp kết quả và thảo luận — chưa hoàn tất", "Hiệu quả, hạn chế, đánh đổi tài nguyên và phạm vi áp dụng; không suy rộng quá bằng chứng", "pending")
    for b,x in [("detectors",26),("agents",74)]:d.edge("fixed",b,[(50,27),(50,32),(x,32),(x,38)])
    d.edge("detectors","metrics");d.edge("metrics","limits")
    d.edge("agents","technical");d.edge("technical","ratings",pending=True)
    for b,x in [("limits",26),("ratings",74)]:d.edge(b,"synthesis",[(x,121),(x,130)],pending=True)
    ds.append(d)
    return ds


def mmd(d):
    lines=[f"%% {d.title}", f"%% Chapter {d.chapter}; Vietnamese draft as of {AS_OF}.",
           "%% Generated from render_diagrams.py. Semantic source; automatic layout may differ.", "flowchart TB"]
    for n in d.nodes:
        label=html.escape(n['title']+("\n"+n['body'] if n['body'] else ""),quote=True).replace("\n","<br/>")
        lines.append(f'    {n["id"]}["{label}"]:::{n["kind"]}')
    for e in d.edges:
        arrow="-.->" if e["pending"] else "-->"
        label="|"+html.escape(e['label']).replace('\n','<br/>')+"|" if e['label'] else ""
        lines.append(f"    {e['start']} {arrow}{label} {e['end']}")
    for k,(fill,stroke) in COLORS.items():
        dash=",stroke-dasharray:6 4" if k=="pending" else ""
        lines.append(f"    classDef {k} fill:{fill},stroke:{stroke},color:{INK}{dash};")
    lines.extend("%% "+f for f in d.footer)
    return "\n".join(lines)+"\n"


def render(d):
    fig,ax=plt.subplots(figsize=(d.width*.074,d.height*.074),dpi=100)
    fig.subplots_adjust(left=0,right=1,top=1,bottom=0)
    ax.set(xlim=(0,d.width),ylim=(d.height,0));ax.axis('off')
    all_text=[]; node_text=[]
    def text(x,y,s,fs=12.5,bold=False,**kw):
        available=FT2Font(font(bold).get_file()).get_charmap()
        missing=sorted({c for c in s if not c.isspace() and ord(c) not in available})
        if missing:raise ValueError(f"Unsupported font glyphs in {d.stem}: {missing}")
        a=ax.text(x,y,s,fontproperties=font(bold),fontsize=fs,color=INK,
                  ha="center",va="center",linespacing=1.2,zorder=5,**kw)
        all_text.append(a);return a
    text(d.width/2,3.5,d.title,16,True)
    text(d.width/2,8.5,d.subtitle,10.6)
    for n in d.nodes:
        fill,edge=COLORS[n['kind']]
        patch=FancyBboxPatch((n['x'],n['y']),n['w'],n['h'],boxstyle="round,pad=0.06,rounding_size=.7",
            linewidth=1.1,edgecolor=edge,facecolor=fill,linestyle=(0,(5,3)) if n['kind']=='pending' else 'solid',zorder=3)
        ax.add_patch(patch)
        # Physical text height in points; figure/data scale is fixed at .074 inch/unit.
        title_h=len(n['title'].splitlines())*n['hs']*1.2/5.328
        body_h=len(n['body'].splitlines())*n['fs']*1.2/5.328 if n['body'] else 0
        gap=.9 if body_h else 0
        y=n['y']+(n['h']-title_h-body_h-gap)/2
        title=text(n['x']+n['w']/2,y+title_h/2,n['title'],n['hs'],True)
        body=text(n['x']+n['w']/2,y+title_h+gap+body_h/2,n['body'],n['fs']) if body_h else None
        node_text.append((n,patch,title,body))
    for e in d.edges:
        pts=e['points'];color=COLORS['pending'][1] if e['pending'] else INK
        style=(0,(5,3)) if e['pending'] else 'solid'
        for a,b in zip(pts[:-2],pts[1:-1]):
            ax.plot([a[0],b[0]],[a[1],b[1]],color=color,lw=1.0,linestyle=style,zorder=2)
        ax.add_patch(FancyArrowPatch(pts[-2],pts[-1],arrowstyle='-|>',mutation_scale=11,
            linewidth=1.0,color=color,linestyle=style,shrinkA=0,shrinkB=.8,zorder=4))
        if e['label'] and e['at']:
            text(*e['at'],e['label'],e['fs'],bbox=dict(facecolor='white',edgecolor='none',pad=.6))
    for t in d.labels:text(t['x'],t['y'],t['text'],t['fs'],t['bold'])
    footer_start=d.height-4-(len(d.footer)-1)*3.3
    if d.legend:
        y=footer_start-4
        ax.plot([8,13],[y,y],color=INK,lw=1.1)
        text(31,y,"Đã có triển khai",10.4)
        ax.plot([57,62],[y,y],color=COLORS['pending'][1],lw=1.1,linestyle=(0,(5,3)))
        text(80,y,"Dự kiến / chưa hoàn tất",10.4)
    for i,s in enumerate(d.footer):text(d.width/2,footer_start+i*3.3,s,10.5)
    fig.canvas.draw();renderer=fig.canvas.get_renderer()
    errors=[]
    for n,patch,title,body in node_text:
        boundary=patch.get_window_extent(renderer)
        for a in [title,body]:
            if a is None:continue
            b=a.get_window_extent(renderer)
            if b.x0<boundary.x0+3 or b.x1>boundary.x1-3 or b.y0<boundary.y0+2 or b.y1>boundary.y1-2:
                errors.append(f"{n['id']}: text outside box: {a.get_text()!r}")
        if body is not None and body.get_window_extent(renderer).y1>title.get_window_extent(renderer).y0-1:
            errors.append(n['id']+": title/body overlap")
    canvas=fig.bbox
    for a in all_text:
        b=a.get_window_extent(renderer)
        if b.x0<2 or b.x1>canvas.width-2 or b.y0<2 or b.y1>canvas.height-2:
            errors.append("outside canvas: "+a.get_text())
    for i,a in enumerate(all_text):
        aa=a.get_window_extent(renderer)
        for b in all_text[i+1:]:
            bb=b.get_window_extent(renderer)
            if min(aa.x1,bb.x1)-max(aa.x0,bb.x0)>1 and min(aa.y1,bb.y1)-max(aa.y0,bb.y0)>1:
                errors.append(f"Overlapping text: {a.get_text()!r} / {b.get_text()!r}")
    # Check route segments do not pass through an unrelated node's interior.
    for e in d.edges:
        for a,b in zip(e['points'][:-1],e['points'][1:]):
            for n in d.nodes:
                if n['id'] in [e['start'],e['end']]:continue
                x0,x1=n['x']+.25,n['x']+n['w']-.25
                y0,y1=n['y']+.25,n['y']+n['h']-.25
                crosses=(abs(a[0]-b[0])<1e-8 and x0<a[0]<x1 and max(min(a[1],b[1]),y0)<min(max(a[1],b[1]),y1)) or (
                    abs(a[1]-b[1])<1e-8 and y0<a[1]<y1 and max(min(a[0],b[0]),x0)<min(max(a[0],b[0]),x1))
                if crosses:errors.append(f"edge {e['start']}->{e['end']} crosses {n['id']}")
    if errors:
        plt.close(fig)
        raise ValueError(d.stem+"\n"+"\n".join(errors))
    # Glyph paths preserve Vietnamese across Word/browser font installations.
    fig.savefig(HERE/(d.stem+'.svg'),facecolor='white',metadata={"Date":AS_OF,"Description":d.caption})
    fig.savefig(HERE/(d.stem+'.png'),facecolor='white',dpi=300)
    plt.close(fig)
    (HERE/(d.stem+'.mmd')).write_text(mmd(d),encoding='utf-8')
    (HERE/(d.stem+'.layout.json')).write_text(json.dumps(d.__dict__,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with Image.open(HERE/(d.stem+'.png')) as im: width_px,height_px=im.size
    return {"stem":d.stem,"chapter":d.chapter,"title":d.title,"caption":d.caption,
        "width_px":width_px,"height_px":height_px,
        "node_count":len(d.nodes),"edge_count":len(d.edges),"layout_checks":"passed",
        "sources":[{"path":p,"sha256":hashlib.sha256((ROOT/p).read_bytes()).hexdigest()} for p in d.sources],
        "outputs":[d.stem+ext for ext in ['.svg','.png','.mmd','.layout.json']]}


def gallery(ds):
    cards=[]
    for d in ds:
        cards.append(f'<section id="{d.stem}"><h2>{html.escape(d.title)}</h2>'
            f'<p>Vị trí đề xuất: Chương {d.chapter}. {html.escape(d.caption)}</p>'
            f'<p><a href="{d.stem}.svg">SVG</a> · <a href="{d.stem}.png">PNG 300 dpi</a> · '
            f'<a href="{d.stem}.mmd">Mermaid</a></p><img src="{d.stem}.svg" alt="{html.escape(d.title)}"></section>')
    doc='''<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bộ sơ đồ khóa luận KLTN-Lon2G</title><style>
body{margin:0;background:#edf1f3;color:#20384a;font-family:Georgia,'Times New Roman',serif;line-height:1.5}
header,main{max-width:1100px;margin:auto;padding:24px}h1{font-size:28px}h2{font-size:22px}
section{background:white;margin:0 0 28px;padding:24px;border:1px solid #d8e0e5;border-radius:10px}
img{display:block;width:100%;height:auto;max-height:1100px;object-fit:contain}a{color:#245b80}
@media print{body{background:white}header{display:none}section{break-after:page;border:0}img{max-height:85vh}}
</style><header><h1>11 sơ đồ tiếng Việt cho khóa luận</h1>
<p>Bản vẽ theo hiện trạng 09/11/2026, chờ duyệt nội dung. Chưa đánh số hình chính thức.
Nét đứt chỉ phần dự kiến; nét liền không có nghĩa toàn bộ hệ thống đã hoàn thiện.</p>
<p>Đây là thư viện hình tĩnh, không phải dashboard của đề tài. Không gọi API hoặc tạo dữ liệu thực nghiệm.</p>
</header><main>'''+''.join(cards)+'</main></html>'
    (HERE/'index.html').write_text(doc,encoding='utf-8')
    thumb_w,thumb_h=550,690
    sheet=Image.new('RGB',(thumb_w*3,thumb_h*4),'#edf1f3')
    draw=ImageDraw.Draw(sheet);f=ImageFont.truetype(font(True).get_file(),19)
    for i,d in enumerate(ds):
        x=(i%3)*thumb_w;y=(i//3)*thumb_h
        im=Image.open(HERE/(d.stem+'.png')).convert('RGB')
        im.thumbnail((thumb_w-24,thumb_h-55))
        sheet.paste(im,(x+(thumb_w-im.width)//2,y+42))
        draw.text((x+15,y+10),f'{i+1:02d} · Chương {d.chapter}',fill=INK,font=f)
    sheet.save(HERE/'contact_sheet.vi.png')


def main():
    plt.rcParams.update({'svg.fonttype':'path','svg.hashsalt':'kltn-thesis-diagrams-v1','axes.unicode_minus':False})
    ds=definitions()
    artifacts=[]
    for d in ds:
        artifacts.append(render(d));print('Rendered:',d.stem,flush=True)
    gallery(ds)
    manifest={"as_of":AS_OF,"language":"vi","approval_status":"draft_pending_user_review",
        "scope":"eleven_diagrams_not_experimental_charts","new_experiments":False,"api_calls":0,
        "figure_numbering":"unassigned_until_chapter_layout_is_final",
        "font":font().get_file(),"dpi":300,"source_of_truth":"render_diagrams.py",
        "generator_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"figures":artifacts}
    (HERE/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Complete:',len(ds),'diagrams; gallery, contact sheet and provenance manifest.')


if __name__=='__main__':main()
