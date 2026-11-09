// Build a thesis chapter .docx following the UEL (Khoa HTTT, 20/12/2024) formatting rules.
// Usage: node scripts/thesis/build_chapter.js <config.json>
// Config: {src, out, docTitle, refsTitle, tableWidths: [[dxa,...],...], tableFontSize?, figures: {"Hình 3.1": png}}
// Source syntax: # / ## / ### headings, pipe tables (---: = right aligned), **Bảng x.y.** / **Hình x.y.** captions,
// "Nguồn:" / "Ghi chú:" lines, "> [Vị trí chèn Hình x.y]" image markers, "- " bullets, "1. " numbered lists,
// "$$ expression || (x.y)" numbered formulas, and "@@REFS" followed by APA entries (*italic*).
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, Table, TableRow,
  TableCell, WidthType, BorderStyle, ShadingType, Footer, PageNumber, ImageRun,
  LevelFormat, VerticalAlign, TabStopType,
} = require("docx");

const CFG = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const base = path.dirname(path.resolve(process.argv[2]));
const SRC = path.resolve(base, CFG.src), OUT = path.resolve(base, CFG.out);
const FONT = "Times New Roman";
const SIZE = 26; // 13pt
const LINE = 360; // 1.5 lines
const CONTENT_WIDTH = 9071; // A4 21 cm - 3 cm left - 2 cm right

function runs(text, extra = {}) {
  // **bold** and `code` (code rendered as plain text)
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index), ...extra }));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(new TextRun({ text: tok.slice(2, -2), ...extra, bold: true }));
    else out.push(new TextRun({ text: tok.slice(1, -1), ...extra }));
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last), ...extra }));
  return out;
}

const body = (text) => new Paragraph({
  children: runs(text), alignment: AlignmentType.JUSTIFIED,
  indent: { firstLine: 567 }, spacing: { line: LINE, before: 0, after: 120 },
});

function captionRuns(text) {
  const m = text.match(/^\*\*([^*]+)\*\*\s*(.*)$/);
  if (!m) return runs(text);
  return [new TextRun({ text: m[1] + " ", bold: true }), ...runs(m[2])];
}

const note = (text) => new Paragraph({
  children: [new TextRun({ text, italics: true })], alignment: AlignmentType.LEFT,
  spacing: { line: LINE, before: 60, after: 200 },
});

const border = { style: BorderStyle.SINGLE, size: 4, color: "000000" };
const borders = { top: border, bottom: border, left: border, right: border };

function table(rows, widths) {
  if (!widths || widths.length !== rows[0].length) throw new Error("tableWidths mismatch for table: " + rows[0].map((c) => c.text).join(" | "));
  return new Table({
    width: { size: CONTENT_WIDTH, type: WidthType.DXA },
    columnWidths: widths,
    rows: rows.map((cells, ri) => new TableRow({
      tableHeader: ri === 0, cantSplit: true,
      children: cells.map((c, ci) => new TableCell({
        borders, width: { size: widths[ci], type: WidthType.DXA },
        verticalAlign: VerticalAlign.CENTER,
        shading: ri === 0 ? { fill: "E7EEF5", type: ShadingType.CLEAR, color: "auto" } : undefined,
        margins: { top: 60, bottom: 60, left: 100, right: 100 },
        children: [new Paragraph({
          children: runs(c.text, { ...(ri === 0 ? { bold: true } : {}), ...(CFG.tableFontSize ? { size: CFG.tableFontSize } : {}) }),
          alignment: ri === 0 ? AlignmentType.CENTER : (c.align === "right" ? AlignmentType.RIGHT : AlignmentType.LEFT),
          spacing: { line: 276, before: 0, after: 0 },
        })],
      })),
    })),
  });
}

function apaRuns(text) {
  const out = []; const re = /\*([^*]+)\*/g; let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index) }));
    out.push(new TextRun({ text: m[1], italics: true })); last = m.index + m[0].length;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last) }));
  return out;
}

const lines = fs.readFileSync(SRC, "utf8").split("\n");
const children = [];
let i = 0, tIdx = 0, inRefs = false, listInstance = 1;
while (i < lines.length) {
  const line = lines[i];
  if (!line.trim()) { i++; continue; }
  if (line === "@@REFS") {
    inRefs = true;
    children.push(new Paragraph({ pageBreakBefore: true, alignment: AlignmentType.CENTER, spacing: { after: 240, line: LINE },
      children: [new TextRun({ text: CFG.refsTitle, bold: true })] }));
    i++; continue;
  }
  if (inRefs) {
    children.push(new Paragraph({ children: apaRuns(line), alignment: AlignmentType.LEFT,
      indent: { left: 720, hanging: 720 }, spacing: { line: LINE, after: 120 } }));
    i++; continue;
  }
  if (line.startsWith("# ")) {
    children.push(new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(line.slice(2))] }));
  } else if (line.startsWith("## ")) {
    children.push(new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(line.slice(3))] }));
  } else if (line.startsWith("### ")) {
    children.push(new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(line.slice(4))] }));
  } else if (line.startsWith("|")) {
    const block = [];
    while (i < lines.length && lines[i].startsWith("|")) block.push(lines[i++]);
    const parse = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((s) => s.trim());
    const align = parse(block[1]).map((s) => (s.endsWith(":") ? "right" : "left"));
    const rows = [block[0], ...block.slice(2)].map((l) => parse(l).map((t, k) => ({ text: t, align: align[k] })));
    children.push(table(rows, CFG.tableWidths[tIdx++]));
    continue;
  } else if (/^\*\*Bảng /.test(line)) {
    children.push(new Paragraph({ children: captionRuns(line), alignment: AlignmentType.CENTER, keepNext: true,
      spacing: { line: LINE, before: 120, after: 60 } }));
  } else if (/^\*\*Hình /.test(line)) {
    children.push(new Paragraph({ children: captionRuns(line), alignment: AlignmentType.CENTER,
      spacing: { line: LINE, before: 60, after: 0 } }));
  } else if (line.startsWith("Nguồn:") || line.startsWith("Ghi chú:")) {
    children.push(note(line));
  } else if (line.startsWith("> [Vị trí chèn Hình")) {
    const label = line.match(/Hình \d+\.\d+/)[0];
    const img = fs.readFileSync(path.resolve(base, CFG.figures[label]));
    const pw = img.readUInt32BE(16), ph = img.readUInt32BE(20);
    let w = 460, h = Math.round(w * ph / pw);
    if (h > 640) { h = 640; w = Math.round(h * pw / ph); }
    children.push(new Paragraph({ alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 120, after: 60 },
      children: [new ImageRun({ type: "png", data: img, transformation: { width: w, height: h },
        altText: { title: label, description: label, name: label } })] }));
  } else if (line.startsWith("$$ ")) {
    const [expr, num] = line.slice(3).split("||").map((x) => x.trim());
    children.push(new Paragraph({ tabStops: [{ type: TabStopType.CENTER, position: 4535 }, { type: TabStopType.RIGHT, position: 9071 }],
      spacing: { line: LINE, before: 60, after: 120 },
      children: [new TextRun({ text: "\t" + expr, italics: true }), new TextRun({ text: "\t" + num })] }));
  } else if (/^- /.test(line)) {
    children.push(new Paragraph({ numbering: { reference: "bullets", level: 0 }, children: runs(line.slice(2)),
      alignment: AlignmentType.JUSTIFIED, spacing: { line: LINE, after: 80 } }));
  } else if (/^\d+\. /.test(line)) {
    children.push(new Paragraph({ numbering: { reference: "numbered", level: 0, instance: listInstance },
      children: runs(line.replace(/^\d+\. /, "")), alignment: AlignmentType.JUSTIFIED, spacing: { line: LINE, after: 80 } }));
    if (!/^\d+\. /.test(lines[i + 1] || "")) listInstance++;
  } else {
    children.push(body(line));
  }
  i++;
}
if (tIdx !== CFG.tableWidths.length) throw new Error(`Config has ${CFG.tableWidths.length} table widths, source has ${tIdx} tables`);

const doc = new Document({
  creator: "Huỳnh Quốc Long",
  title: CFG.docTitle,
  styles: {
    default: { document: { run: { font: FONT, size: SIZE }, paragraph: { spacing: { line: LINE } } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: 28, bold: true, allCaps: true, color: "000000" },
        paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 0, after: 360, line: LINE }, keepNext: true, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: SIZE, bold: true, color: "000000" },
        paragraph: { spacing: { before: 240, after: 120, line: LINE }, keepNext: true, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: SIZE, bold: true, italics: true, color: "000000" },
        paragraph: { spacing: { before: 180, after: 120, line: LINE }, keepNext: true, outlineLevel: 2 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "–", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numbered", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    ],
  },
  sections: [{
    properties: {
      page: { size: { width: 11906, height: 16838 },
        margin: { top: 1134, bottom: 1134, left: 1701, right: 1134, footer: 567 },
        pageNumbers: { start: 1 } },
    },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [new TextRun({ children: [PageNumber.CURRENT] })] })] }) },
    children,
  }],
});

Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(OUT, buf); console.log("written", OUT); });
