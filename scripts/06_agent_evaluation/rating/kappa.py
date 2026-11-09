"""Inter-rater agreement for the explanation rubric.

Reads one or more filled rating CSVs (one per rater, same review_id rows) and reports,
per criterion: number of rated items, observed agreement, and
  - 2 raters: Cohen's kappa and quadratic-weighted kappa (+ case-bootstrap 95% interval);
  - >=3 raters: Fleiss' kappa.
Kappa measures agreement between raters, not correctness of an explanation.

    .venv/bin/python -B scripts/06_agent_evaluation/rating/kappa.py rater_A.csv rater_B.csv
"""
import csv
import json
import random
import sys
from pathlib import Path

from sklearn.metrics import cohen_kappa_score

CATEGORIES = ['1', '2', '3', '4', '5']
RUBRIC = json.loads((Path(__file__).resolve().parent / 'rubric_v2.json').read_text())
CRITERIA = [c['id'] for c in RUBRIC['criteria']]


def fleiss_kappa(table):
    """table[i][j] = number of raters assigning item i to category j (equal raters per item)."""
    n_items = len(table)
    if n_items == 0:
        return None
    n = sum(table[0])
    if n < 2 or any(sum(row) != n for row in table):
        raise ValueError('Fleiss kappa needs the same number (>=2) of ratings per item')
    p_j = [sum(row[j] for row in table) / (n_items * n) for j in range(len(table[0]))]
    p_i = [(sum(c * c for c in row) - n) / (n * (n - 1)) for row in table]
    p_bar, p_e = sum(p_i) / n_items, sum(p * p for p in p_j)
    return None if p_e == 1 else (p_bar - p_e) / (1 - p_e)


def read_sheet(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    return {r['review_id']: r for r in rows}


def clean(v):
    v = (v or '').strip().upper()
    return None if v in {'', 'NA'} else v


def agreement(sheets, criterion, seed=20261109, boot=2000):
    ids = sorted(set.intersection(*(set(s) for s in sheets)))
    values = [[clean(s[i].get(criterion)) for s in sheets] for i in ids]
    for row in values:
        for v in row:
            if v is not None and v not in CATEGORIES:
                raise ValueError(f'Invalid rating {v!r} for {criterion}')
    complete = [row for row in values if all(v is not None for v in row)]
    out = {'criterion': criterion, 'items_in_all_sheets': len(ids), 'items_rated_by_all': len(complete),
           'excluded_with_NA_or_blank': len(ids) - len(complete), 'raters': len(sheets)}
    if not complete:
        return out
    out['observed_exact_agreement'] = sum(len(set(r)) == 1 for r in complete) / len(complete)
    if len(sheets) == 2:
        a, b = [int(r[0]) for r in complete], [int(r[1]) for r in complete]
        same = len(set(a) | set(b)) == 1
        out['cohen_kappa'] = None if same else cohen_kappa_score(a, b, labels=[1, 2, 3, 4, 5])
        out['weighted_kappa_quadratic'] = None if same else cohen_kappa_score(a, b, labels=[1, 2, 3, 4, 5], weights='quadratic')
        rng, draws = random.Random(seed), []
        for _ in range(boot):
            idx = [rng.randrange(len(a)) for _ in a]
            aa, bb = [a[i] for i in idx], [b[i] for i in idx]
            if len(set(aa) | set(bb)) > 1:
                draws.append(cohen_kappa_score(aa, bb, labels=[1, 2, 3, 4, 5], weights='quadratic'))
        if len(draws) >= boot * 0.9:
            draws.sort()
            out['weighted_kappa_quadratic_bootstrap_95'] = [draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]]
        out['method'] = 'Cohen (1960); weighted, Cohen (1968)'
    else:
        table = [[row.count(c) for c in CATEGORIES] for row in complete]
        out['fleiss_kappa'] = fleiss_kappa(table)
        out['method'] = 'Fleiss (1971), nominal categories'
    return out


def main(paths):
    if len(paths) < 2:
        raise SystemExit('Need at least two rater sheets')
    sheets = [read_sheet(p) for p in paths]
    report = {'rubric': RUBRIC['version'], 'sheets': [Path(p).name for p in paths],
              'results': [agreement(sheets, c) for c in CRITERIA],
              'interpretation_limit': RUBRIC['agreement']['interpretation_limit']}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main(sys.argv[1:])
