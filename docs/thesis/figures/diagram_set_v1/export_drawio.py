"""Export the 11 thesis diagrams to editable draw.io files.

Reads the `.layout.json` files written by render_diagrams.py (same boxes,
coordinates, edges, labels and footers) and writes one `.drawio` file per
diagram into ./drawio/. Geometry uses the renderer scale (0.074 inch per unit,
96 px per inch) so font sizes match the PNG export.

Run from the project root:
    .venv/bin/python -B docs/thesis/figures/diagram_set_v1/export_drawio.py
"""
import html
import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
OUT = HERE / "drawio"
S = 0.074 * 96            # px per layout unit
PT = 96 / 72              # pt -> px
FONT = "Times New Roman"
INK = "#20384A"
COLORS = {
    "done": ("#F1F6FA", "#284E68"),
    "pending": ("#FFF7E8", "#946425"),
    "note": ("#F5F5F3", "#656C71"),
    "alert": ("#FBF0EF", "#98524B"),
    "output": ("#EFF6F2", "#396852"),
}


def px(v):
    return round(v * S, 1)


def fpx(pt):
    return round(pt * PT, 1)


def text_html(s):
    return "<br>".join(html.escape(line, quote=False) for line in s.split("\n"))


def style(**kv):
    return ";".join(f"{k}={v}" for k, v in kv.items()) + ";"


def nearest_on_path(points, at):
    """Relative label position (-1..1 along path) and offset in px for point `at`."""
    segs = list(zip(points[:-1], points[1:]))
    lengths = [math.dist(a, b) for a, b in segs]
    total = sum(lengths) or 1
    best = None
    walked = 0
    for (a, b), L in zip(segs, lengths):
        if L == 0:
            continue
        t = max(0, min(1, ((at[0]-a[0])*(b[0]-a[0]) + (at[1]-a[1])*(b[1]-a[1])) / L**2))
        p = (a[0] + t*(b[0]-a[0]), a[1] + t*(b[1]-a[1]))
        dist = math.dist(p, at)
        if best is None or dist < best[0]:
            best = (dist, (walked + t*L) / total, p)
        walked += L
    _, frac, p = best
    return round(2*frac - 1, 4), px(at[0]-p[0]), px(at[1]-p[1])


def build(layout):
    W, H = layout["width"], layout["height"]
    nodes = {n["id"]: n for n in layout["nodes"]}
    mxfile = ET.Element("mxfile", host="drawio", agent="KLTN-Lon2G export_drawio.py", version="24.0.0")
    diagram = ET.SubElement(mxfile, "diagram", id=layout["stem"], name=layout["title"])
    model = ET.SubElement(diagram, "mxGraphModel", dx="0", dy="0", grid="1", gridSize="10", guides="1",
                          tooltips="1", connect="1", arrows="1", fold="1", page="1", pageScale="1",
                          pageWidth=str(math.ceil(px(W))), pageHeight=str(math.ceil(px(H))),
                          background="#FFFFFF", math="0", shadow="0")
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")
    counter = [0]

    def cell_id(prefix):
        counter[0] += 1
        return f"{prefix}-{counter[0]}"

    def geometry(parent, **attrs):
        g = ET.SubElement(parent, "mxGeometry", attrib={k: str(v) for k, v in attrs.items()})
        g.set("as", "geometry")
        return g

    def text(x_center, y_center, s, pt, bold=False, width=None, cid=None):
        lines = s.split("\n")
        h = len(lines) * pt * 1.25 * PT + 4
        w = px(width) if width else px(W) - 8
        c = ET.SubElement(root, "mxCell", id=cid or cell_id("text"), value=text_html(s), vertex="1", parent="1",
                          style=style(text=None, html=1, align="center", verticalAlign="middle", whiteSpace="wrap",
                                      fontFamily=FONT, fontSize=fpx(pt), fontColor=INK, fontStyle=1 if bold else 0,
                                      spacing=0).replace("text=None", "text"))
        geometry(c, x=round(px(x_center) - w/2, 1), y=round(px(y_center) - h/2, 1), width=w, height=round(h, 1))

    text(W/2, 3.5, layout["title"], 16, True, cid="title")
    text(W/2, 8.5, layout["subtitle"], 10.6, cid="subtitle")

    for n in layout["nodes"]:
        fill, stroke = COLORS[n["kind"]]
        value = f'<b><font style="font-size:{fpx(n["hs"])}px">{text_html(n["title"])}</font></b>'
        if n["body"]:
            value += f'<div style="margin-top:4px">{text_html(n["body"])}</div>'
        st = dict(rounded=1, arcSize=4, absoluteArcSize=0, whiteSpace="wrap", html=1, fillColor=fill,
                  strokeColor=stroke, strokeWidth=1.1, fontColor=INK, fontFamily=FONT, fontSize=fpx(n["fs"]),
                  align="center", verticalAlign="middle", spacing=4)
        if n["kind"] == "pending":
            st.update(dashed=1, dashPattern="5 3")
        c = ET.SubElement(root, "mxCell", id=n["id"], value=value, vertex="1", parent="1", style=style(**st))
        geometry(c, x=px(n["x"]), y=px(n["y"]), width=px(n["w"]), height=px(n["h"]))

    for e in layout["edges"]:
        pts = [tuple(p) for p in e["points"]]
        for a, b in zip(pts[:-1], pts[1:]):
            if abs(a[0]-b[0]) > 1e-6 and abs(a[1]-b[1]) > 1e-6:
                raise ValueError(f'{layout["stem"]}: diagonal segment {e["start"]}->{e["end"]}')
        src, dst = nodes[e["start"]], nodes[e["end"]]
        rel = lambda p, n: (round((p[0]-n["x"])/n["w"], 4), round((p[1]-n["y"])/n["h"], 4))
        ex, ey = rel(pts[0], src)
        nx, ny = rel(pts[-1], dst)
        color = COLORS["pending"][1] if e["pending"] else INK
        st = dict(edgeStyle="orthogonalEdgeStyle", rounded=0, html=1, endArrow="block", endFill=1, endSize=6,
                  strokeColor=color, strokeWidth=1, fontFamily=FONT, fontSize=fpx(e["fs"]), fontColor=INK,
                  labelBackgroundColor="#FFFFFF", exitX=ex, exitY=ey, exitDx=0, exitDy=0, exitPerimeter=0,
                  entryX=nx, entryY=ny, entryDx=0, entryDy=0, entryPerimeter=0)
        if e["pending"]:
            st.update(dashed=1, dashPattern="5 3")
        c = ET.SubElement(root, "mxCell", id=cell_id("edge"), value=text_html(e["label"]), edge="1", parent="1",
                          source=e["start"], target=e["end"], style=style(**st))
        g = ET.SubElement(c, "mxGeometry", relative="1")
        g.set("as", "geometry")
        if e["label"] and e["at"]:
            rx, ox, oy = nearest_on_path(pts, e["at"])
            g.set("x", str(rx))
            off = ET.SubElement(g, "mxPoint", x=str(ox), y=str(oy))
            off.set("as", "offset")
        if len(pts) > 2:
            arr = ET.SubElement(g, "Array")
            arr.set("as", "points")
            for p in pts[1:-1]:
                ET.SubElement(arr, "mxPoint", x=str(px(p[0])), y=str(px(p[1])))

    for t in layout["labels"]:
        text(t["x"], t["y"], t["text"], t["fs"], t["bold"], width=20)

    footer_start = H - 4 - (len(layout["footer"]) - 1) * 3.3
    if layout["legend"]:
        y = footer_start - 4
        for x0, x1, label, lx, pending in [(8, 13, "Đã có triển khai", 31, False),
                                           (57, 62, "Dự kiến / chưa hoàn tất", 80, True)]:
            st = dict(endArrow="none", html=1, strokeColor=COLORS["pending"][1] if pending else INK, strokeWidth=1.1)
            if pending:
                st.update(dashed=1, dashPattern="5 3")
            c = ET.SubElement(root, "mxCell", id=cell_id("legend"), value="", edge="1", parent="1", style=style(**st))
            g = ET.SubElement(c, "mxGeometry", relative="1")
            g.set("as", "geometry")
            for name, x in [("sourcePoint", x0), ("targetPoint", x1)]:
                p = ET.SubElement(g, "mxPoint", x=str(px(x)), y=str(px(y)))
                p.set("as", name)
            text(lx, y, label, 10.4, width=30)
    for i, s in enumerate(layout["footer"]):
        text(W/2, footer_start + i*3.3, s, 10.5)
    return mxfile


def main():
    OUT.mkdir(exist_ok=True)
    files = sorted(HERE.glob("*.layout.json"))
    for f in files:
        layout = json.loads(f.read_text(encoding="utf-8"))
        tree = ET.ElementTree(build(layout))
        ET.indent(tree)
        target = OUT / (layout["stem"] + ".drawio")
        tree.write(target, encoding="utf-8", xml_declaration=False)
        print("Wrote", target.relative_to(HERE))
    print(f"Complete: {len(files)} draw.io files")


if __name__ == "__main__":
    main()
