"""Compatibility checks on real Olist rows; no research outputs or labels written."""

import ast
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import legacy_anomaly_helpers as legacy
from scripts import reporting


class CodeCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.discovery = importlib.import_module("scripts.02_process_mining.process_mining_analysis")
        cls.conformance = importlib.import_module("conformance_analysis")
        cls.data_audit = importlib.import_module("scripts.01_data_preparation.data_quality_audit")
        cls.manual_audit = importlib.import_module("scripts.03_anomaly_detection.manual_validation_audit")
        cls.refinement = importlib.import_module("scripts.03_anomaly_detection.experiments.anomaly_refinement_experiment")
        cls.candidate = importlib.import_module("scripts.03_anomaly_detection.experiments.anomaly_final_candidate_experiment")
        cls.fit = pd.read_csv(ROOT/"data/experiments/bed_bath_table_v1/train_timing_features.csv", nrows=128)
        cls.features = cls.candidate.CANDIDATE_IF_FEATURES
        cls.events = pd.read_csv(ROOT/"data/experiments/olist_integrated_evidence_v1/visible_events.csv",
                                 usecols=["order_id", "split", "recorded_timestamp", "activity"])
        train = cls.events.loc[cls.events.split.eq("train")]
        cls.events = train.loc[train.order_id.isin(train.order_id.drop_duplicates().head(32))].rename(
            columns={"order_id": "case_id", "recorded_timestamp": "timestamp"})[["case_id", "activity", "timestamp"]]

    def test_original_public_names_share_implementations(self):
        self.assertIs(self.discovery.prepare_pm4py_dataframe, self.conformance.prepare_pm4py_dataframe)
        self.assertIs(self.discovery.read_event_log, self.conformance.read_event_log)
        self.assertIs(self.data_audit.format_report_value, reporting.format_value)
        self.assertIs(self.manual_audit.format_value, reporting.format_value)
        for module, name in [(self.refinement, "run_experimental_if"), (self.candidate, "run_candidate_if")]:
            self.assertIs(getattr(module, name), legacy.run_legacy_if)
            self.assertIs(module.prepare_feature_matrix, legacy.prepare_feature_matrix)
            self.assertIs(module.jaccard, legacy.jaccard)

    def test_real_event_log_conversion_preserves_input_and_variants(self):
        original = self.events.copy(deep=True)
        expected = self.conformance.prepare_pm4py_dataframe(original)
        actual = self.discovery.prepare_pm4py_dataframe(self.events)
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)
        pd.testing.assert_frame_equal(self.events, original, check_exact=True)
        self.assertEqual(len(actual), len(original))
        variants = self.discovery.get_sorted_variants(actual)
        self.assertEqual(sum(count for _, count in variants), original.case_id.nunique())

    def test_event_log_reader_retains_its_default_and_custom_path(self):
        self.assertEqual(self.discovery.INPUT_PATH, self.conformance.INPUT_PATH)
        with patch.object(self.conformance.pd, "read_csv", return_value=self.events) as read:
            self.assertIs(self.discovery.read_event_log(), self.events)
            read.assert_called_once_with(self.conformance.INPUT_PATH)
            read.reset_mock()
            custom = ROOT/"data/experiments/olist_integrated_evidence_v1/visible_events.csv"
            self.discovery.read_event_log(custom)
            read.assert_called_once_with(custom)

    def test_real_training_matrix_matches_historical_calculation(self):
        original = self.fit.copy(deep=True)
        expected = StandardScaler().fit_transform(SimpleImputer(strategy="median").fit_transform(self.fit[self.features]))
        actual = legacy.prepare_feature_matrix(self.fit, self.features)
        np.testing.assert_array_equal(actual, expected)
        pd.testing.assert_frame_equal(self.fit, original, check_exact=True)

    def test_legacy_if_flags_match_on_real_training_rows_only(self):
        matrix = legacy.prepare_feature_matrix(self.fit, self.features)
        for contamination in [.05, .08]:
            with self.subTest(contamination=contamination):
                expected = (IsolationForest(n_estimators=300, contamination=contamination,
                                            random_state=42, n_jobs=-1).fit_predict(matrix) == -1).astype(int)
                np.testing.assert_array_equal(legacy.run_legacy_if(matrix, contamination), expected)

    def test_jaccard_on_observed_duration_comparisons(self):
        first = self.fit[self.features[0]].gt(self.fit[self.features[0]].median())
        second = self.fit[self.features[1]].gt(self.fit[self.features[1]].median())
        self.assertEqual(legacy.jaccard(first, second), (first & second).sum()/(first | second).sum())
        empty = first.iloc[:0]
        self.assertTrue(np.isnan(legacy.jaccard(empty, empty)))
        none = self.fit[self.features[0]].lt(0)
        self.assertFalse(none.any())
        self.assertTrue(np.isnan(legacy.jaccard(none, none)))

    def test_report_format_matches_frozen_original_on_real_scalars(self):
        path = ROOT/"scripts/03_anomaly_detection/auto_validation.py"
        tree = ast.parse(path.read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "format_value")
        namespace = {"pd": pd}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
        values = self.fit[self.features].head(8).to_numpy().ravel().tolist()
        values += self.events.iloc[0].tolist()
        values += [None, pd.NA, np.nan]
        for value in values:
            self.assertEqual(reporting.format_value(value), namespace["format_value"](value))

    def test_imports_do_not_read_data_write_outputs_or_fit(self):
        paths = ["01_data_preparation/data_quality_audit.py", "02_process_mining/process_mining_analysis.py",
                 "03_anomaly_detection/manual_validation_audit.py",
                 "03_anomaly_detection/experiments/anomaly_refinement_experiment.py",
                 "03_anomaly_detection/experiments/anomaly_final_candidate_experiment.py"]
        program = """
import runpy, sys
from unittest.mock import patch
import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest
with patch.object(pd, 'read_csv', side_effect=AssertionError('Import must not read data')), \\
     patch.object(pd.DataFrame, 'to_csv', side_effect=AssertionError('Import must not write data')), \\
     patch.object(joblib, 'dump', side_effect=AssertionError('Import must not save models')), \\
     patch.object(IsolationForest, 'fit', side_effect=AssertionError('Import must not train')):
    for path in sys.argv[1:]:
        runpy.run_path(path, run_name='cleanup_import_check')
"""
        result = subprocess.run([sys.executable, "-c", program, *[str(ROOT/"scripts"/p) for p in paths]],
                                cwd=ROOT, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_current_trainers_do_not_import_legacy_helpers(self):
        names = ["train_imported_process", "train_business_ablation", "benchmark_bed_bath_table", "train_bed_bath_table"]
        for name in names:
            tree = ast.parse((ROOT/"scripts/03_anomaly_detection"/(name+".py")).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("legacy_anomaly_helpers", node.module or "")
                elif isinstance(node, ast.Import):
                    self.assertFalse(any("legacy_anomaly_helpers" in alias.name for alias in node.names))

    def test_saved_manifest_code_hashes_remain_valid(self):
        scripts = {str(path.relative_to(ROOT)): path for path in (ROOT/"scripts").rglob("*.py") if path.name != "__init__.py"}
        by_name = {path.name: name for name, path in scripts.items()}
        checked = set()

        def walk(value, context=""):
            if isinstance(value, dict):
                for key, item in value.items():
                    if "code" in context.lower() or "engine_hash" in context.lower():
                        name = key if key in scripts else by_name.get(key)
                        if name and isinstance(item, str) and len(item) == 64:
                            with scripts[name].open("rb") as stream:
                                self.assertEqual(hashlib.file_digest(stream, "sha256").hexdigest(), item, name)
                            checked.add(name)
                    walk(item, context+"/"+key)
            elif isinstance(value, list):
                for item in value:
                    walk(item, context)

        for directory in ["data", "models", "outputs"]:
            for path in (ROOT/directory).rglob("*.json"):
                if "manifest" in path.name:
                    walk(json.loads(path.read_text()))
        self.assertGreaterEqual(len(checked), 39)


if __name__ == "__main__":
    unittest.main()
