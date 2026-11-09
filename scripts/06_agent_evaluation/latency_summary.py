"""Latency summary from saved measurements only (no new run, no API call).

Components (measured separately; not one production end-to-end measurement):
  1. event replay: apply + score + commit per event (olist_event_stage_scoring_v1/processing_times.csv);
  2. AI explanation per case: wall time of each comparison slot (olist_agent_comparison_v3), or the
     07/11 live pair if the comparison has not been run;
  3. dashboard build: static page generation time (measured here, offline).
The sum of component medians is an indicative order of magnitude, not an SLA.

    .venv/bin/python -B scripts/06_agent_evaluation/latency_summary.py
"""
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'data/experiments'


def q(values, p):
    values = sorted(values)
    return None if not values else values[min(len(values) - 1, int(round(p * (len(values) - 1))))]


def replay():
    t = pd.read_csv(EXP / 'olist_event_stage_scoring_v1/processing_times.csv')['apply_score_commit_ms']
    rt = json.loads((EXP / 'olist_event_stage_scoring_v1/runtime.json').read_text())
    return {'events': int(t.size), 'ms_p50': float(t.quantile(.5)), 'ms_p95': float(t.quantile(.95)), 'ms_max': float(t.max()),
            'events_per_second': rt['events_per_loop_second'], 'definition': rt['definition'], 'excluded': rt['excluded']}


def ai():
    v3 = EXP / 'olist_agent_comparison_v4/slots'
    rows = []
    if v3.exists():
        saved = {json.loads(f.read_text())['slot']['slot']: json.loads(f.read_text()) for f in sorted(v3.glob('*.json'))}
        for f in sorted((v3.parent / 'replacements').glob('*.json')):
            r = json.loads(f.read_text())
            saved[r['slot']['replacement_of']] = r  # technical-interruption replacement
        for s in saved.values():
            rows.append({'arm': s['slot']['arm'], 'profile': s['slot']['profile'], 'status': s['result']['explanation_status'],
                         'seconds': s['result']['explanation_seconds'], 'responses': s['accounting']['model_responses_received']})
        source = 'olist_agent_comparison_v4 (with technical-interruption replacement)'
    else:
        summary = json.loads((EXP / 'olist_agent_live_review_v2_case2/summary.json').read_text())
        for arm, v in summary['arms'].items():
            rows.append({'arm': arm, 'profile': 'native_bounded', 'status': v['metrics'].get('status'), 'seconds': v['seconds'],
                         'responses': v['accounting']['model_responses_received']})
        source = 'olist_agent_live_review_v2_case2 (one live pair, 07/11/2026)'
    groups = {}
    for r in rows:
        groups.setdefault((r['arm'], r['profile']), []).append(r)
    out = []
    for (arm, profile), rs in sorted(groups.items()):
        ok = [r['seconds'] for r in rs if r['status'] in ('evidence_checked', 'evidence_checked_with_rejections', 'code_checked')]
        allsec = [r['seconds'] for r in rs]
        out.append({'arm': arm, 'profile': profile, 'runs': len(rs), 'runs_with_published_output': len(ok),
                    'seconds_p50_all': statistics.median(allsec), 'seconds_p95_all': q(allsec, .95),
                    'seconds_p50_published': statistics.median(ok) if ok else None,
                    'responses_p50': statistics.median(r['responses'] for r in rs)})
    return {'source': source, 'groups': out,
            'note': 'Wall time includes token counting, network and local tools; stopped runs are time-to-stop.'}


def dashboard():
    started = time.perf_counter()
    subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/06_dashboard/build_dashboard.py')], check=True, capture_output=True)
    return {'build_seconds': time.perf_counter() - started, 'note': 'Static page rebuilt from saved outputs; browser render excluded.'}


def main():
    report = {'replay_per_event': replay(), 'ai_per_case': ai(), 'dashboard': dashboard()}
    three = [g for g in report['ai_per_case']['groups'] if g['arm'] == 'three_agents' and g['profile'] == 'native_bounded']
    if three:
        report['indicative_warning_to_explanation_seconds'] = {
            'value': report['replay_per_event']['ms_p50'] / 1000 + three[0]['seconds_p50_all'],
            'meaning': 'median replay step + median three-agent explanation; components measured separately, not an SLA'}
    out = ROOT / 'docs/latency_summary.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
