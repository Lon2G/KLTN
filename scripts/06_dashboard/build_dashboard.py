"""Local BI dashboard (static HTML, works offline from file://) built only from saved real outputs.

Views: management overview -> groups (stage, customer state, seller) -> orders -> order evidence,
for two datasets:
  test        retrospective holdout 06-08/2018 with the frozen candidate (olist_selected_holdout_v1)
  validation  event replay 03-05/2018, scenario assumed_static_context_at_purchase
              (olist_event_stage_scoring_v1); milestones after the replay cutoff are hidden.
Saved AI explanations are attached to an order only when a verified output exists.
No model call, no new scoring, no thresholds changed.

    .venv/bin/python -B scripts/06_dashboard/build_dashboard.py
"""
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/06_agent_evaluation/rating'))
from render_vi import output_sections, STATUS_VI  # noqa: E402
import render_en  # noqa: E402

EXP = ROOT / 'data/experiments'
OUT = ROOT / 'data/dashboard'
STAGE_ORDER = ['approval', 'preparation_handoff', 'transport']
TS = ['order_purchase_timestamp', 'order_approved_at', 'order_delivered_carrier_date',
      'order_delivered_customer_date', 'order_estimated_delivery_date']


def num(v, digits=3):
    return None if pd.isna(v) else round(float(v), digits)


def stage_rows(df):
    out = {}
    for r in df.itertuples(index=False):
        warn = r.long_stage_warning
        out.setdefault(r.order_id, {})[r.stage] = {
            'a': r.assessment_status if hasattr(r, 'assessment_status') else None,
            'd': num(r.duration_days), 'rm': num(r.selected_reference_mean_days), 'rn': None if pd.isna(r.selected_reference_n) else int(r.selected_reference_n),
            'sc': None if pd.isna(r.selected_reference_scope) else r.selected_reference_scope,
            'z': num(r.delay_score_0_100, 2), 'sev': None if pd.isna(r.severity) else r.severity,
            'w': None if pd.isna(warn) else bool(warn) if not isinstance(warn, str) else warn == 'True'}
    return out


def test_dataset():
    d = EXP / 'olist_selected_holdout_v1'
    stages = pd.read_csv(d / 'stage_scores.csv')
    stages['assessment_status'] = stages['assessable'].map({True: 'assessed', False: 'not_assessable'})
    stages.loc[stages['delay_score_0_100'].isna(), 'assessment_status'] = 'unassessed'
    ledger = pd.read_csv(d / 'order_ledger.csv')
    per_stage = stage_rows(stages)
    orders = []
    for r in ledger.itertuples(index=False):
        orders.append({'id': r.order_id, 'm': r.purchase_month, 'st': r.customer_state,
                       'sel': None if pd.isna(r.single_seller_id) else r.single_seller_id,
                       'multi': bool(not pd.isna(r.mapped_seller_count) and r.mapped_seller_count > 1),
                       'dlv': r.performance_state, 'pd': num(r.promise_delta_calendar_days, 0),
                       'ts': {k: (None if pd.isna(getattr(r, k)) else str(getattr(r, k))) for k in TS},
                       'stg': per_stage.get(r.order_id, {})})
    return {'key': 'test', 'label': 'Test hồi cứu 06–08/2018', 'label_en': 'Retrospective test 06–08/2018', 'cutoff': None,
            'note': 'Ứng viên cố định product_context_stage_score_gt0; đánh giá hồi cứu, đích đối chiếu là giao sau ngày dự kiến.',
            'note_en': 'Frozen candidate product_context_stage_score_gt0; retrospective evaluation; reference target is delivery after the promised date.',
            'orders': orders}


def validation_dataset():
    stages = pd.read_csv(EXP / 'olist_event_stage_scoring_v1/stage_scores.csv')
    stages = stages[stages['mode'] == 'assumed_static_context_at_purchase']
    cohort = pd.read_csv(EXP / 'bed_bath_table_v1/cohort_cases.csv')
    cohort = cohort[cohort['split'] == 'validation']
    perf = pd.read_csv(EXP / 'olist_delivery_performance_v1/order_performance.csv')
    perf = perf[perf['split'] == 'validation'].set_index('order_id')
    per_stage = stage_rows(stages)
    cutoff = '2018-06-01 00:00:00'
    orders = []
    for r in cohort.itertuples(index=False):
        if r.order_id not in per_stage:
            continue
        ts = {}
        for k in TS:
            v = getattr(r, k)
            # Estimated date is known at purchase; event milestones after the replay cutoff are hidden.
            ts[k] = None if pd.isna(v) or (k != 'order_estimated_delivery_date' and str(v) >= cutoff) else str(v)
        p = perf.loc[r.order_id] if r.order_id in perf.index else None
        # Delivery state comes from the saved delivery analysis (its own cutoff), not recomputed here.
        if p is None:
            dlv = 'not_in_delivery_analysis'
        elif p['performance_state'] == 'unknown' and p['delivery_visibility'] == 'at_or_after_cutoff':
            dlv = 'not_observed_at_cutoff'
        else:
            dlv = p['performance_state']
        orders.append({'id': r.order_id, 'm': r.purchase_month, 'st': r.customer_state,
                       'sel': None if pd.isna(r.single_seller_id) else r.single_seller_id,
                       'multi': bool(r.seller_count > 1), 'dlv': dlv,
                       'pd': num(p['promise_delta_calendar_days'], 0) if p is not None else None,
                       'ts': ts, 'stg': per_stage[r.order_id]})
    return {'key': 'validation', 'label': 'Validation – phát lại sự kiện 03–05/2018', 'label_en': 'Validation – event replay 03–05/2018', 'cutoff': cutoff,
            'note_en': 'Replay of 7,076 real milestones; scenario assumes static context was available at purchase (unverified). Milestones after 2018-06-01 are hidden. Delivery status comes from the saved delivery analysis with its own cutoff ' + str(perf['visible_before_exclusive'].iloc[0])[:10] + '; not recomputed.',
            'note': 'Phát lại 7.076 mốc thật; kịch bản giả định bối cảnh tĩnh có sẵn lúc đặt hàng (chưa xác minh). Mốc sau 01/06/2018 bị ẩn. Trạng thái giao hàng lấy từ phân tích giao hàng đã lưu với mốc cắt riêng ' + str(perf['visible_before_exclusive'].iloc[0])[:10] + '; không tính lại.',
            'orders': orders}


def explanations():
    """Verified saved AI outputs by order id (latest version available)."""
    found = {}
    sources = [(EXP / 'olist_agent_live_review_v2_case2', 'three_agents.json', 'review_v2, lượt thử API 07/11/2026')]
    v3 = EXP / 'olist_agent_comparison_v4/slots'
    cohort = {r['case_key']: r['order_id'] for r in json.loads((EXP / 'olist_agent_comparison_v1/cohort.json').read_text())}
    plan = json.loads((sources[0][0] / 'plan.json').read_text())
    for folder, name, label in sources:
        p = folder / name
        if p.exists():
            res = json.loads(p.read_text())
            found[plan['case']['order_id']] = (label, res, 'review_v2, live API trial 2026-11-07')
    if v3.exists():
        for f in sorted(v3.glob('*__three_agents__native_bounded.json')):
            saved = json.loads(f.read_text())
            found[cohort[saved['slot']['case_key']]] = ('review_v4, đợt đối chứng 20 case', saved['result'], 'review_v4, 20-case comparison run')
    out = {}
    for oid, (label, res, label_en) in found.items():
        sec = output_sections(res)
        en = render_en.output_sections(res)
        if res['explanation_status'] in {'evidence_checked', 'evidence_checked_with_rejections', 'code_checked'}:
            out[oid] = {'label': label, 'status': STATUS_VI.get(sec['status'], sec['status']),
                        'claims': sec['claims'], 'notes': sec['notes'], 'checks': sec['checks'],
                        'rejected': len(res.get('rejected_claims', [])) + len(res.get('rejected_metadata', [])),
                        'en': {'label': label_en, 'status': render_en.STATUS_EN.get(en['status'], en['status']),
                               'claims': en['claims'], 'notes': en['notes'], 'checks': en['checks']}}
    return out


def main():
    data = {'datasets': [test_dataset(), validation_dataset()], 'ai': explanations(),
            'built_from': ['data/experiments/olist_selected_holdout_v1', 'data/experiments/olist_event_stage_scoring_v1',
                           'data/experiments/bed_bath_table_v1/cohort_cases.csv',
                           'data/experiments/olist_delivery_performance_v1/order_performance.csv']}
    OUT.mkdir(parents=True, exist_ok=True)
    template = (Path(__file__).resolve().parent / 'template.html').read_text(encoding='utf-8')
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    (OUT / 'index.html').write_text(template.replace('/*__DATA__*/null', payload), encoding='utf-8')
    print(json.dumps({'out': str(OUT / 'index.html'), 'datasets': {d['key']: len(d['orders']) for d in data['datasets']},
                      'ai_explanations': len(data['ai'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
