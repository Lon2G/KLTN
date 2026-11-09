"""Dashboard data equals saved experiment outputs; nothing is rescored."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/06_dashboard'))
import build_dashboard as bd  # noqa: E402

STAGES = ('approval', 'preparation_handoff', 'transport')


def counts(orders):
    assessed = [o for o in orders if any(o['stg'].get(s, {}).get('z') is not None for s in STAGES)]
    warned = [o for o in orders if any(o['stg'].get(s, {}).get('w') is True for s in STAGES)]
    stage_warn = sum(o['stg'].get(s, {}).get('w') is True for o in orders for s in STAGES)
    return len(orders), len(assessed), len(warned), stage_warn


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test, cls.val = bd.test_dataset(), bd.validation_dataset()

    def test_test_holdout_matches_saved_metrics(self):
        n, assessed, warned, _ = counts(self.test['orders'])
        self.assertEqual(n, 1691)
        summary = json.loads((bd.EXP / 'olist_selected_holdout_v1/summary.json').read_text())
        self.assertEqual(n, summary['total_test_orders'])
        self.assertEqual(warned, 70)            # alerts in validation_test_metrics.csv (test_all)
        early_warned = [o for o in self.test['orders'] if o['dlv'] == 'early'
                        and any(o['stg'].get(s, {}).get('w') is True for s in STAGES)]
        self.assertEqual(len(early_warned), 58)  # figure quoted in Chapter 1
        self.assertGreaterEqual(assessed, 1523)

    def test_validation_replay_matches_saved_summary(self):
        n, assessed, warned, stage_warn = counts(self.val['orders'])
        self.assertEqual((n, assessed, warned, stage_warn), (1853, 1726, 221, 225))

    def test_future_milestones_hidden_in_replay(self):
        cutoff = self.val['cutoff']
        for o in self.val['orders']:
            for k, v in o['ts'].items():
                if v is not None and k != 'order_estimated_delivery_date':
                    self.assertLess(v, cutoff)

    def test_missing_scores_never_become_zero(self):
        for o in self.test['orders'] + self.val['orders']:
            for s in o['stg'].values():
                if s['z'] is None:
                    self.assertIsNot(s['w'], False)


if __name__ == '__main__':
    unittest.main()
