"""Chapter 5 charts, drawn only from saved experiment outputs (no new computation of metrics).

    .venv/bin/python -B docs/thesis/figures/chapter5/make_charts.py
"""
import json
from pathlib import Path
import glob

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
EXP = ROOT / 'data/experiments'
FONT = '/System/Library/Fonts/Supplemental/Times New Roman.ttf'
fp = FontProperties(fname=FONT) if Path(FONT).exists() else FontProperties(family='DejaVu Serif')
plt.rcParams.update({'font.family': fp.get_name(), 'font.size': 11, 'axes.spines.top': False, 'axes.spines.right': False,
                     'axes.edgecolor': '#8391a2', 'axes.labelcolor': '#20384a', 'xtick.color': '#20384a', 'ytick.color': '#20384a'})
if Path(FONT).exists():
    matplotlib.font_manager.fontManager.addfont(FONT)
    plt.rcParams['font.family'] = 'Times New Roman'
from matplotlib.ticker import FuncFormatter
COMMA = FuncFormatter(lambda v, _: f'{v:.1f}'.replace('.', ','))
BLUE, ORANGE, AQUA, GRAY = '#2a78d6', '#eb6834', '#1baf7a', '#b6c0cc'


def vi(x, d=2):
    return f'{x:.{d}f}'.replace('.', ',')


def label_bars(ax, bars, d=2):
    for b in bars:
        h = b.get_height()
        ax.annotate(vi(h, d), (b.get_x() + b.get_width() / 2, h), xytext=(0, 3), textcoords='offset points',
                    ha='center', va='bottom', fontsize=9, color='#20384a')


def fig_families():
    f = pd.read_csv(EXP / 'olist_delivery_discrimination_v1/family_summary.csv')
    names = {'upper_quantile': 'Phân vị trên', 'isolation_forest': 'Isolation Forest', 'product_context_stage': 'Tham chiếu theo\nsản phẩm/bối cảnh',
             'lof': 'LOF', 'one_class_svm': 'One-Class SVM', 'iqr': 'IQR', 'ensemble': 'Ensemble\nthứ hạng', 'mad': 'MAD'}
    f = f.sort_values('best_auc', ascending=False)
    x = range(len(f))
    fig, ax = plt.subplots(figsize=(8.6, 3.9), dpi=300)
    b1 = ax.bar([i - 0.2 for i in x], f.best_auc, 0.38, color=BLUE, label='ROC AUC tốt nhất')
    b2 = ax.bar([i + 0.2 for i in x], f.best_ap, 0.38, color=ORANGE, label='Average Precision tốt nhất')
    label_bars(ax, b1); label_bars(ax, b2)
    ax.set_xticks(list(x), [names[k] for k in f.family], fontsize=9.5)
    ax.set_ylim(0, 1.05); ax.set_ylabel('Giá trị (0–1)'); ax.yaxis.set_major_formatter(COMMA)
    ax.grid(axis='y', color='#e4e8ee'); ax.set_axisbelow(True)
    ax.legend(frameon=False, loc='upper right', ncol=2)
    fig.tight_layout(); fig.savefig(HERE / 'fig5_1_method_families.png'); plt.close(fig)


def fig_val_test():
    m = pd.read_csv(EXP / 'olist_selected_holdout_v1/validation_test_metrics.csv')
    val = m[m.scope == 'validation_all'].iloc[0]; test = m[m.scope == 'test_all'].iloc[0]
    keys = [('precision', 'Precision'), ('recall', 'Recall'), ('f1_raw', 'F1'), ('roc_auc', 'ROC AUC'), ('average_precision', 'Average\nPrecision')]
    x = range(len(keys))
    fig, ax = plt.subplots(figsize=(7.6, 3.7), dpi=300)
    b1 = ax.bar([i - 0.2 for i in x], [val[k] for k, _ in keys], 0.38, color=BLUE, label=f'Validation (n = {int(val.orders):,}, tỷ lệ trễ {vi(100*val.positive_prevalence,1)}%)'.replace(',', '.', 1))
    b2 = ax.bar([i + 0.2 for i in x], [test[k] for k, _ in keys], 0.38, color=ORANGE, label=f'Test (n = {int(test.orders):,}, tỷ lệ trễ {vi(100*test.positive_prevalence,1)}%)'.replace(',', '.', 1))
    label_bars(ax, b1); label_bars(ax, b2)
    ax.set_xticks(list(x), [l for _, l in keys]); ax.set_ylim(0, 1.05); ax.set_ylabel('Giá trị (0–1)'); ax.yaxis.set_major_formatter(COMMA); ax.yaxis.set_major_formatter(COMMA)
    ax.grid(axis='y', color='#e4e8ee'); ax.set_axisbelow(True); ax.legend(frameon=False, loc='upper left', fontsize=9.5)
    fig.tight_layout(); fig.savefig(HERE / 'fig5_2_validation_test.png'); plt.close(fig)


def load(v):
    out = {}
    for p in glob.glob(str(EXP / f'olist_agent_comparison_{v}/slots/*.json')):
        s = json.load(open(p)); out[s['slot']['slot']] = s
    for p in glob.glob(str(EXP / f'olist_agent_comparison_{v}/replacements/*.json')):
        s = json.load(open(p)); out[s['slot']['replacement_of']] = s
    return out


def fig_ai():
    pub = {'code_checked', 'evidence_checked', 'evidence_checked_with_rejections'}
    arms = [('single_agent', 'native_bounded', 'Một tác tử\nnative'), ('single_agent', 'matched_4', 'Một tác tử\nmatched'),
            ('three_agents', 'native_bounded', 'Ba tác tử\nnative'), ('three_agents', 'matched_4', 'Ba tác tử\nmatched')]
    vals = {}
    for v in ('v3', 'v4'):
        d = load(v)
        vals[v] = [sum(s['result']['explanation_status'] in pub for s in d.values() if s['slot']['arm'] == a and s['slot']['profile'] == p) for a, p, _ in arms]
    x = range(len(arms) + 1)
    fig, ax = plt.subplots(figsize=(7.6, 3.7), dpi=300)
    rules = ax.bar([0], [20], 0.5, color=GRAY, label='Quy tắc cố định')
    b1 = ax.bar([i + 1 - 0.2 for i in range(len(arms))], vals['v3'], 0.38, color=BLUE, label='v3 (trần 6.000 token)')
    b2 = ax.bar([i + 1 + 0.2 for i in range(len(arms))], vals['v4'], 0.38, color=ORANGE, label='v4 (trần 12.000 token)')
    for bars in (rules, b1, b2):
        for b in bars:
            ax.annotate(str(int(b.get_height())), (b.get_x() + b.get_width() / 2, b.get_height()), xytext=(0, 3),
                        textcoords='offset points', ha='center', fontsize=9.5)
    ax.set_xticks(list(x), ['Quy tắc\ncố định'] + [l for *_, l in arms]); ax.set_ylim(0, 23); ax.set_ylabel('Số case có đầu ra công bố (trên 20)')
    ax.grid(axis='y', color='#e4e8ee'); ax.set_axisbelow(True); ax.legend(frameon=False, loc='upper center', bbox_to_anchor=(0.5, 1.13), ncol=3, fontsize=9.5)
    fig.tight_layout(); fig.savefig(HERE / 'fig5_3_ai_completion.png'); plt.close(fig)
    return vals


if __name__ == '__main__':
    fig_families(); fig_val_test(); print(fig_ai())
