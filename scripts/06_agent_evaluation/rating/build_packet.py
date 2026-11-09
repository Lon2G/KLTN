"""Blinded human-review packet for the 20-case explanation comparison.

For each case: the shared detector table, then 5 outputs (rules, single agent and three
agents under two budget profiles) in a per-case shuffled order labelled A–E. Raters get
packet.html + their own empty CSV. The arm key stays with the study coordinator;
--share-out creates a separate folder and ZIP containing only the HTML and blank CSVs.

    .venv/bin/python -B scripts/06_agent_evaluation/rating/build_packet.py --out DIR [--raters 2]
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import random
import shutil
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from render_vi import output_sections, detector_rows, STATUS_VI, esc  # noqa: E402

COMPARISON_V1 = ROOT / 'data/experiments/olist_agent_comparison_v1'
COMPARISON_V3 = ROOT / 'data/experiments/olist_agent_comparison_v4'
RUBRIC = json.loads((HERE / 'rubric_v2.json').read_text())
ARMS = [('rules', 'shared_control'), ('single_agent', 'native_bounded'), ('three_agents', 'native_bounded'),
        ('single_agent', 'matched_4'), ('three_agents', 'matched_4')]


def load_output(case_key, arm, profile):
    if arm == 'rules':
        p = COMPARISON_V1 / 'rules_results' / f'{case_key}.json'
        return json.loads(p.read_text()) if p.exists() else None
    name = f'{case_key}__{arm}__{profile}.json'
    for p in (COMPARISON_V3 / 'replacements' / name, COMPARISON_V3 / 'slots' / name):
        if p.exists():
            return json.loads(p.read_text())['result']
    return None


def review_id(case_key, arm, profile):
    return 'rv-' + hashlib.sha256(f'rating_v1|{case_key}|{arm}|{profile}'.encode()).hexdigest()[:16]


def build(out, raters, allow_partial):
    if raters < 2:
        raise ValueError('Independent review needs at least two raters')
    # Never erase submitted scores when regenerating the packet.
    for sheet in out.glob('rating_sheet_rater*.csv'):
        ensure_blank_sheet(sheet)
    cohort = json.loads((COMPARISON_V1 / 'cohort.json').read_text())
    cases, key, missing = [], [], 0
    for no, row in enumerate(cohort, 1):
        order = ARMS[:]
        random.Random(hashlib.sha256(('labels|' + row['case_key']).encode()).hexdigest()).shuffle(order)
        detector, outputs = None, []
        for label, (arm, profile) in zip('ABCDE', order):
            result = load_output(row['case_key'], arm, profile)
            missing += result is None
            detector = detector or (result or {}).get('detector')
            rid = review_id(row['case_key'], arm, profile)
            outputs.append({'label': label, 'review_id': rid, 'sections': output_sections(result)})
            key.append({'review_id': rid, 'case_no': no, 'case_key': row['case_key'], 'label': label,
                        'arm': arm, 'profile': profile, 'output_available': result is not None})
        cases.append({'no': no, 'detector': detector, 'outputs': outputs})
    if missing and not allow_partial:
        raise SystemExit(f'{missing} outputs are not available yet; run the comparison first or pass --allow-partial')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'packet.html').write_text(render_html(cases, missing, raters), encoding='utf-8')
    fields = ['review_id', 'case_no', 'label'] + [c['id'] for c in RUBRIC['criteria']] + ['notes']
    for r in range(1, raters + 1):
        with open(out / f'rating_sheet_rater{r}.csv', 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for c in cases:
                for o in c['outputs']:
                    w.writerow({'review_id': o['review_id'], 'case_no': c['no'], 'label': o['label']})
    (out / 'rating_key.json').write_text(json.dumps({'warning': 'Tài liệu nội bộ: không gửi cho người chấm. Chỉ giải mã sau khi lưu cả hai phiếu chấm độc lập.', 'rows': key},
                                                    ensure_ascii=False, indent=2), encoding='utf-8')
    return {'cases': len(cases), 'outputs': len(key), 'missing_outputs': missing, 'raters': raters, 'out': str(out)}


def ensure_blank_sheet(path):
    with path.open(newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            if any((value or '').strip() for name, value in row.items()
                   if name not in {'review_id', 'case_no', 'label'}):
                raise ValueError(f'Rating sheet already contains scores or notes: {path}')


def export_for_raters(out, share_out, raters):
    """Allowlist only; never copy the key, result JSONs, or another rater's scores."""
    names = ['packet.html'] + [f'rating_sheet_rater{r}.csv' for r in range(1, raters + 1)]
    archive = share_out.parent / f'{share_out.name}.zip'
    if share_out.exists() or archive.exists():
        raise FileExistsError('Choose a new share directory; existing review packages are preserved')
    for name in names:
        if not (out / name).is_file():
            raise FileNotFoundError(out / name)
        if name.endswith('.csv'):
            ensure_blank_sheet(out / name)
    share_out.mkdir(parents=True)
    for name in names:
        shutil.copyfile(out / name, share_out / name)
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name in names:
            z.write(share_out / name, arcname=name)
    return {'directory': str(share_out), 'zip': str(archive), 'files': names}


def render_html(cases, missing, raters=2):
    crit = ''.join(f"<tr><td><b>{esc(c['vi'])}</b><br><code>{esc(c['id'])}</code></td><td>{esc(c['guide'])}</td></tr>"
                   for c in RUBRIC['criteria'])
    scale = ' · '.join(f'{k} = {esc(v)}' for k, v in RUBRIC['scale'].items())
    parts = [f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Phiếu chấm giải thích</title><style>
body{{font-family:'Times New Roman',serif;max-width:1000px;margin:auto;padding:16px;color:#1d2b36;line-height:1.45}}
table{{border-collapse:collapse;width:100%;margin:8px 0}}td,th{{border:1px solid #b8c4cc;padding:4px 8px;vertical-align:top;font-size:15px}}
th{{background:#e7eef5}}.case{{border-top:3px solid #284e68;margin-top:28px;padding-top:8px}}
.out{{border:1px solid #b8c4cc;border-radius:6px;padding:8px 12px;margin:10px 0;background:#fafbfc}}
.out h4{{margin:0 0 4px}}ul{{margin:4px 0 4px 20px;padding:0}}.empty{{color:#98524b}}.warn{{color:#98524b;font-weight:bold}}
</style></head><body><h1>Phiếu chấm chất lượng giải thích (bản chấm ẩn danh)</h1>
<p>Mỗi đơn có một bảng dữ liệu phát hiện giống nhau cho mọi phương án, và năm đầu ra A–E. Thứ tự A–E được xáo riêng cho từng đơn.
Chấm từng đầu ra theo thang 1–5 trên file CSV; dùng NA khi không đánh giá được và ghi lý do. Đây là đánh giá giải thích, <b>không phải</b> nhãn tắc nghẽn.</p>
<h2>Hướng dẫn cho {raters} người chấm độc lập</h2>
<ol>
<li>Mỗi người chấm toàn bộ <b>{len(cases)} đơn × 5 đầu ra = {len(cases) * 5} mục</b> theo cùng bộ tiêu chí; không chia đôi mẫu.</li>
<li>Người chấm số 1 điền <code>rating_sheet_rater1.csv</code>; người chấm số 2 điền <code>rating_sheet_rater2.csv</code>.
Mỗi người chỉ sửa phiếu mang số của mình. Nếu có thêm người chấm, dùng phiếu mang số tương ứng.</li>
<li>Đối chiếu <code>case_no</code> với số đơn và <code>label</code> với đầu ra A–E trong trang này.
Giữ nguyên <code>review_id</code>, <code>case_no</code>, <code>label</code> và tên các cột; không xóa hoặc thêm dòng.</li>
<li>Điền đủ {len(RUBRIC['criteria'])} tiêu chí cho từng mục bằng số nguyên 1–5 hoặc <code>NA</code>.
Nếu dùng NA, ghi rõ tiêu chí và lý do trong <code>notes</code>. Đầu ra kém hoặc thiếu nội dung cần được chấm theo rubric, không tự động ghi NA.</li>
<li>Không trao đổi điểm, không xem phiếu đã điền của người khác và không tìm phương án đứng sau nhãn A–E trước khi nộp.
Mỗi người gửi riêng CSV của mình cho người phụ trách; không gửi cho người chấm còn lại.</li>
<li>Phiếu ban đầu được giữ nguyên. Nếu cần thảo luận bất đồng, chỉ thực hiện sau khi đã nhận đủ các phiếu độc lập và lưu kết quả thảo luận riêng.</li>
</ol>
<p><b>Thang điểm:</b> {scale}</p><table><tr><th>Tiêu chí</th><th>Hướng dẫn</th></tr>{crit}</table>
<ul>{''.join(f'<li>{esc(r)}</li>' for r in RUBRIC['rater_rules'])}</ul>"""]
    if missing:
        parts.append(f'<p class="warn">Bản xem trước: {missing} đầu ra chưa có vì đợt đối chứng AI chưa chạy.</p>')
    for c in cases:
        parts.append(f"<div class='case'><h2>Đơn {c['no']}</h2>")
        if c['detector']:
            parts.append('<table><tr><th>Công đoạn</th><th>Trạng thái</th><th>Thời lượng (ngày)</th><th>TB tham chiếu</th>'
                         '<th>Số đơn tham chiếu</th><th>Điểm</th><th>Cảnh báo</th></tr>' + ''.join(
                f"<tr><td>{esc(r['stage'])}</td><td>{esc(r['status'])}</td><td>{esc(r['duration'])}</td><td>{esc(r['reference_mean'])}</td>"
                f"<td>{esc(r['reference_n'])}</td><td>{esc(r['score'])}</td><td>{esc(r['warning'])}</td></tr>" for r in detector_rows(c['detector'])) + '</table>')
        for o in c['outputs']:
            s = o['sections']
            parts.append(f"<div class='out'><h4>Đầu ra {o['label']}</h4>")
            if s is None:
                parts.append("<p class='empty'>Chưa có đầu ra (chưa chạy).</p></div>")
                continue
            parts.append(f"<p>Trạng thái: {esc(STATUS_VI.get(s['status'], s['status']))}</p>")
            for title, items in (('Nhận định', s['claims']), ('Giới hạn / chưa biết', s['notes']), ('Đề xuất kiểm tra', s['checks'])):
                body = ''.join(f'<li>{esc(i)}</li>' for i in items) if items else "<li class='empty'>(không có)</li>"
                parts.append(f'<b>{title}</b><ul>{body}</ul>')
            parts.append('</div>')
        parts.append('</div>')
    parts.append('</body></html>')
    return '\n'.join(parts)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--raters', type=int, default=2)
    ap.add_argument('--share-out', type=Path, help='New directory for HTML + blank CSVs only; also creates a ZIP beside it')
    ap.add_argument('--allow-partial', action='store_true')
    a = ap.parse_args()
    info = build(a.out, a.raters, a.allow_partial)
    if a.share_out:
        info['share'] = export_for_raters(a.out, a.share_out, a.raters)
    print(json.dumps(info, ensure_ascii=False))
