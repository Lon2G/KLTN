"""Agreement statistics and the blinded review packet. Ratings here are technical test
inputs, never research ratings."""
import csv
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/06_agent_evaluation/rating'))
import kappa  # noqa: E402
import build_packet  # noqa: E402

# Fleiss (1971)-style worked example widely reproduced (10 items, 14 raters, 5 categories): kappa ~= 0.210.
FLEISS_EXAMPLE = [[0, 0, 0, 0, 14], [0, 2, 6, 4, 2], [0, 0, 3, 5, 6], [0, 3, 9, 2, 0], [2, 2, 8, 1, 1],
                  [7, 7, 0, 0, 0], [3, 2, 6, 3, 0], [2, 5, 3, 2, 2], [6, 5, 2, 1, 0], [0, 2, 2, 3, 7]]


class KappaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def sheet(self, name, ratings):
        path = self.tmp / name
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=['review_id'] + kappa.CRITERIA)
            w.writeheader()
            for i, v in enumerate(ratings):
                w.writerow({'review_id': f'r{i}', **{c: v for c in kappa.CRITERIA}})
        return kappa.read_sheet(path)

    def test_fleiss_reference_example(self):
        self.assertAlmostEqual(kappa.fleiss_kappa(FLEISS_EXAMPLE), 0.210, places=3)

    def test_fleiss_rejects_unequal_rater_counts(self):
        with self.assertRaises(ValueError):
            kappa.fleiss_kappa([[1, 1, 0, 0, 0], [3, 0, 0, 0, 0]])

    def test_two_raters_identical_and_na_excluded(self):
        a = self.sheet('a.csv', ['1', '2', '3', '4', '5', 'NA'])
        b = self.sheet('b.csv', ['1', '2', '3', '4', '5', '3'])
        out = kappa.agreement([a, b], kappa.CRITERIA[0], boot=200)
        self.assertEqual(out['items_rated_by_all'], 5)
        self.assertEqual(out['excluded_with_NA_or_blank'], 1)
        self.assertAlmostEqual(out['cohen_kappa'], 1.0)
        self.assertAlmostEqual(out['weighted_kappa_quadratic'], 1.0)

    def test_weighted_kappa_rewards_near_misses(self):
        a = self.sheet('a.csv', ['1', '2', '3', '4', '5', '2', '4', '3'])
        b = self.sheet('b.csv', ['2', '3', '4', '5', '4', '1', '3', '2'])
        out = kappa.agreement([a, b], kappa.CRITERIA[0], boot=200)
        self.assertGreater(out['weighted_kappa_quadratic'], out['cohen_kappa'])

    def test_three_raters_use_fleiss(self):
        sheets = [self.sheet(f'{n}.csv', ['1', '2', '3', '3']) for n in 'abc']
        out = kappa.agreement(sheets, kappa.CRITERIA[0])
        self.assertIn('fleiss_kappa', out)
        self.assertAlmostEqual(out['fleiss_kappa'], 1.0)

    def test_invalid_rating_rejected(self):
        a, b = self.sheet('a.csv', ['7']), self.sheet('b.csv', ['1'])
        with self.assertRaises(ValueError):
            kappa.agreement([a, b], kappa.CRITERIA[0])


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_partial_preview_is_blinded_and_consistent(self):
        info = build_packet.build(self.tmp, raters=2, allow_partial=True)
        self.assertEqual((info['cases'], info['outputs']), (20, 100))
        html = (self.tmp / 'packet.html').read_text()
        for arm in ('three_agents', 'single_agent', 'rules', 'matched_4', 'native_bounded'):
            self.assertNotIn(arm, html)
        key = json.loads((self.tmp / 'rating_key.json').read_text())['rows']
        with open(self.tmp / 'rating_sheet_rater1.csv', encoding='utf-8-sig') as f:
            sheet = list(csv.DictReader(f))
        self.assertEqual([r['review_id'] for r in sheet], [r['review_id'] for r in key])
        positions = {r['arm'] + r['profile']: set() for r in key}
        for r in key:
            positions[r['arm'] + r['profile']].add(r['label'])
        self.assertTrue(all(len(v) > 1 for v in positions.values()))  # order is shuffled across cases

    def test_full_packet_refuses_missing_outputs(self):
        if (build_packet.COMPARISON_V3 / 'slots').exists():
            self.skipTest('comparison outputs exist')
        with self.assertRaises(SystemExit):
            build_packet.build(self.tmp / 'x', raters=2, allow_partial=False)

    def test_shared_packet_excludes_internal_key_and_results(self):
        internal = self.tmp / 'internal'
        build_packet.build(internal, raters=2, allow_partial=True)
        (internal / 'private_results.json').write_text('{"internal": true}')
        share = self.tmp / 'for_raters'
        info = build_packet.export_for_raters(internal, share, raters=2)
        expected = {'packet.html', 'rating_sheet_rater1.csv', 'rating_sheet_rater2.csv'}
        self.assertEqual({p.name for p in share.iterdir()}, expected)
        with zipfile.ZipFile(info['zip']) as z:
            self.assertEqual(set(z.namelist()), expected)
            for name in expected:
                self.assertEqual(z.read(name), (internal / name).read_bytes())
        self.assertTrue((internal / 'rating_key.json').exists())
        self.assertIn('2 người chấm độc lập', (share / 'packet.html').read_text())
        self.assertEqual((share / 'rating_sheet_rater1.csv').read_bytes(),
                         (share / 'rating_sheet_rater2.csv').read_bytes())

    def test_scores_cannot_be_overwritten_or_shared(self):
        internal = self.tmp / 'internal'
        build_packet.build(internal, raters=2, allow_partial=True)
        sheet = internal / 'rating_sheet_rater1.csv'
        with sheet.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            fields, rows = reader.fieldnames, list(reader)
        rows[0]['notes'] = 'Technical fixture: protect returned review, not a research rating'
        with sheet.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        before = sheet.read_bytes()
        with self.assertRaises(ValueError):
            build_packet.build(internal, raters=2, allow_partial=True)
        with self.assertRaises(ValueError):
            build_packet.export_for_raters(internal, self.tmp / 'share', raters=2)
        self.assertEqual(before, sheet.read_bytes())
        self.assertFalse((self.tmp / 'share').exists())


if __name__ == '__main__':
    unittest.main()
