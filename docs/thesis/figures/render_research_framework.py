"""Render the Vietnamese research overview from an editable, explicit layout.

This is a methodological diagram, not a new experiment. It reads only source
metadata for provenance and writes diagram assets beside this script.
Run from the repository root with .venv/bin/python -B <this script>.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "kltn-thesis-mpl"))
os.environ.setdefault("XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "kltn-thesis-cache"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, findfont
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STEM = "research_framework.vi"
AS_OF = "2026-11-08"
INK = "#172F45"
BLUE = "#F0F5F9"
AMBER = "#FFF8EB"
AMBER_EDGE = "#8B6327"

# Coordinates use a fixed 100 x 152 canvas. All diagram text is Vietnamese.
NODES = [
    ("scope", 7, 128, 86, 12, False,
     "1. Xác định vấn đề và cơ sở nghiên cứu",
     "Thời gian xử lý bất thường trong hoàn tất đơn hàng\nTổng quan nghiên cứu → mục tiêu → câu hỏi nghiên cứu"),
    ("data", 7, 111, 86, 12, False,
     "2. Chuẩn bị dữ liệu và phân tích khám phá",
     "Dữ liệu Olist thật · ngành bed_bath_table\nRà soát chất lượng → chuẩn hóa → EDA; giữ dữ liệu gốc"),
    ("split", 7, 94, 86, 12, False,
     "3. Phân chia dữ liệu theo thời gian",
     "Train: huấn luyện và tham chiếu · Validation: so sánh, lựa chọn\nTest: đánh giá ứng viên cố định; không tinh chỉnh trên test"),
    ("detection", 4, 68, 44, 20, False,
     "4. Phát hiện bất thường",
     "Đặc trưng ba công đoạn\nNhóm sản phẩm và bối cảnh\nSo sánh → cố định ứng viên"),
    ("framework", 52, 68, 44, 20, False,
     "5. Khung Multi-Agent",
     "Phát lại và quản lý trạng thái\nPhân tích quy trình → bối cảnh\n→ kiểm chứng bằng chứng\nĐã thử nhóm nhỏ; đang hoàn thiện"),
    ("quantitative", 7, 46, 86, 17, False,
     "6. Thực nghiệm và đánh giá định lượng",
     "Phát hiện: độ bao phủ, ổn định, đối chiếu giao trễ\nGiải thích: quy tắc / một agent / ba agent; nguồn, thời gian, chi phí\nĐã có kết quả; so sánh AI chưa hoàn chỉnh"),
    ("qualitative", 7, 24, 86, 17, True,
     "7. Đánh giá định tính và trình diễn hệ thống",
     "Thang chấm → chấm độc lập → Kappa phù hợp thiết kế\nHoàn thiện giao diện cục bộ và đánh giá tính hữu ích\nChưa hoàn tất"),
    ("discussion", 7, 8, 86, 11, True,
     "8. Tổng hợp, thảo luận và kết luận",
     "Đối chiếu câu hỏi nghiên cứu · giới hạn · hướng phát triển"),
]
EDGES = [
    ("scope", "data"), ("data", "split"),
    ("split", "detection"), ("split", "framework"),
    ("detection", "framework"),
    ("detection", "quantitative"), ("framework", "quantitative"),
    ("quantitative", "qualitative"), ("qualitative", "discussion"),
]
SOURCES = {
    "scope_and_data": "docs/progress_2026-10-28.md",
    "clean_source_contract": "data/processed/olist_clean_v1/README.md",
    "fixed_candidate_evaluation": "data/experiments/olist_selected_holdout_v1/summary.json",
    "event_replay_scoring": "data/experiments/olist_event_stage_scoring_v1/summary.json",
    "latest_agent_trial": "data/experiments/olist_agent_live_review_v2_case2/summary.json",
    "evaluation_design": "templates/agent_comparison_v1/protocol.json",
    "remaining_work": "docs/thesis_readiness_2026-11-07.md",
}


def font(bold=False):
    filename = "Times New Roman Bold.ttf" if bold else "Times New Roman.ttf"
    system = Path("/System/Library/Fonts/Supplemental") / filename
    if system.exists():
        return FontProperties(fname=str(system))
    return FontProperties(fname=findfont(FontProperties(family="DejaVu Serif", weight="bold" if bold else "normal")))


def make_mermaid():
    lines = ["%% Vietnamese draft; evidence status as of " + AS_OF,
             "%% Generated with render_research_framework.py; no experimental data generated.",
             "flowchart TB"]
    for key, _, _, _, _, pending, title, body in NODES:
        label = title + "<br/>" + body.replace("\n", "<br/>")
        lines.append(f'    {key}["{label}"]:::added' if not pending else f'    {key}["{label}"]:::pending')
    for start, end in EDGES:
        operator = "-.->" if end in {"qualitative", "discussion"} else "-->"
        label = "|Ứng viên cố định|" if (start, end) == ("detection", "framework") else ""
        lines.append(f"    {start} {operator}{label} {end}")
    lines.extend([
        "    classDef added fill:#F0F5F9,stroke:#172F45,color:#172F45;",
        "    classDef pending fill:#FFF8EB,stroke:#8B6327,color:#172F45,stroke-dasharray:6 4;",
        "%% Solid: existing implementation/results, not a claim of production readiness.",
        "%% Dashed: planned or incomplete. Near real-time is historical replay with context assumptions.",
    ])
    return "\n".join(lines) + "\n"


def render():
    # Fail if a provenance link no longer exists instead of silently inventing it.
    hashes = {key: {"path": name, "sha256": hashlib.sha256((ROOT / name).read_bytes()).hexdigest()}
              for key, name in SOURCES.items()}
    plt.rcParams.update({"svg.fonttype": "path", "axes.unicode_minus": False})
    fig, ax = plt.subplots(figsize=(7.4, 11.2))
    fig.subplots_adjust(left=.015, right=.985, top=.985, bottom=.015)
    ax.set(xlim=(0, 100), ylim=(-6, 152))
    ax.axis("off")
    text_items = []

    def text(x, y, value, size=12, bold=False, color=INK, va="center", **kwargs):
        artist = ax.text(x, y, value, ha="center", va=va, fontsize=size,
                         fontproperties=font(bold), color=color, linespacing=1.3, **kwargs)
        return artist

    text(50, 148.5, "QUY TRÌNH NGHIÊN CỨU", size=16, bold=True)
    text(50, 143.8, "KLTN-Lon2G · Phạm vi Olist / bed_bath_table · Hiện trạng 08/11/2026", size=10.5)
    boxes = {}
    for key, x, y, w, h, pending, title, body in NODES:
        box = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.12,rounding_size=1.0",
                             linewidth=1.1, edgecolor=AMBER_EDGE if pending else INK,
                             facecolor=AMBER if pending else BLUE,
                             linestyle=(0, (5, 3)) if pending else "solid", zorder=2)
        ax.add_patch(box)
        heading = text(x + w / 2, y + h - 2.2, title, size=12.6, bold=True, zorder=3)
        # Titles and body blocks are independently centered to keep equal margins.
        body_y = y + (h - 4.5) / 2
        content = text(x + w / 2, body_y, body, size=11.8, zorder=3)
        boxes[key] = (x, y, w, h)
        text_items.append((key, box, heading, content))

    def arrow(points, dashed=False):
        for i in range(len(points) - 2):
            a, b = points[i:i+2]
            ax.plot([a[0], b[0]], [a[1], b[1]], color=INK, lw=1.1,
                    linestyle=(0, (5, 3)) if dashed else "solid", zorder=1)
        ax.add_patch(FancyArrowPatch(points[-2], points[-1], arrowstyle="-|>",
                                    mutation_scale=11, linewidth=1.1, color=INK,
                                    linestyle=(0, (5, 3)) if dashed else "solid", zorder=1))

    arrow([(50, 128), (50, 123.3)])
    arrow([(50, 111), (50, 106.3)])
    arrow([(50, 94), (50, 91), (26, 91), (26, 88.3)])
    arrow([(50, 94), (50, 91), (74, 91), (74, 88.3)])
    arrow([(48.2, 77.5), (51.8, 77.5)])
    arrow([(26, 68), (26, 65.5), (50, 65.5), (50, 63.3)])
    arrow([(74, 68), (74, 65.5), (50, 65.5), (50, 63.3)])
    arrow([(50, 46), (50, 41.3)], dashed=True)
    arrow([(50, 24), (50, 19.3)], dashed=True)

    ax.plot([6, 11], [4.1, 4.1], color=INK, lw=1.2)
    text(30.2, 4.1, "Đã có triển khai / kết quả", size=10.5)
    ax.plot([56, 61], [4.1, 4.1], color=AMBER_EDGE, lw=1.2, linestyle=(0, (5, 3)))
    text(79, 4.1, "Dự kiến / chưa hoàn tất", size=10.5)
    text(50, -.3, "Near real-time: phát lại sự kiện lịch sử, có giả định về bối cảnh.", size=10.5)
    text(50, -3.1, "Cảnh báo là dấu hiệu bất thường; chưa xác nhận nguyên nhân tắc nghẽn.", size=10.5)

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for key, box, heading, body in text_items:
        boundary = box.get_window_extent(renderer)
        for artist in (heading, body):
            b = artist.get_window_extent(renderer)
            if b.x0 < boundary.x0 + 3 or b.x1 > boundary.x1 - 3 or b.y0 < boundary.y0 + 2 or b.y1 > boundary.y1 - 2:
                raise ValueError(f"Text outside node: {key}: {artist.get_text()}")
        if body.get_window_extent(renderer).y1 >= heading.get_window_extent(renderer).y0 - 2:
            raise ValueError(f"Heading and body overlap: {key}")
    fig.savefig(HERE / (STEM + ".svg"), facecolor="white", metadata={"Date": AS_OF})
    fig.savefig(HERE / (STEM + ".png"), dpi=300, facecolor="white")
    plt.close(fig)
    (HERE / (STEM + ".mmd")).write_text(make_mermaid(), encoding="utf-8")
    (HERE / (STEM + ".sources.json")).write_text(json.dumps({
        "as_of": AS_OF, "language": "vi", "approval_status": "draft_pending_user_review",
        "figure_kind": "research_process_overview", "new_experiment": False,
        "sources": hashes,
        "advisor_feedback_basis": "User-provided email: add Cohen/Fleiss Kappa; rater design not yet finalized.",
        "font": font().get_file(), "image_dpi": 300,
        "outputs": [STEM + ext for ext in (".mmd", ".svg", ".png")],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Generated", STEM, "as Mermaid, SVG, and PNG (300 dpi). Text bounds checked.")


if __name__ == "__main__":
    render()
