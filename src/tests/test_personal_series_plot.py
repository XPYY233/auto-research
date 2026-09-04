from __future__ import annotations

import unittest

from auto_research.personal.series_plot import MAX_SERIES_ROWS, SeriesPlotError, project_series


class PersonalSeriesPlotTests(unittest.TestCase):
    def project(self, rows, *, columns=None, series=None):
        return project_series(
            columns=columns or [{"name": "x", "unit": "dpa", "role": "independent"},
                                {"name": "y", "unit": "GPa", "role": "dependent"},
                                {"name": "u", "unit": "GPa", "role": "uncertainty"}],
            names=["x", "y", "u"], rows=rows,
            series=series or {"name": "硬度", "x_column": "x", "y_column": "y", "uncertainty_column": "u"})

    def test_missing_invalid_and_uncertainty_preserve_source_rows(self):
        result = self.project([
            ["0", "3.200", "0.050"], ["1", "", "0.1"], ["2", "NaN", "0.1"],
            ["3", "4", "-0.2"], ["4", "5", ""], ["5", "6", "unknown"],
            ["6", "7"], ["−1", "1e-3", "0.01"], ["8", "1e9999", "0"],
            ["9", "1,234", "0"], ["10", "1e-9999", "0"],
        ])
        self.assertEqual(result["total_rows"], 11)
        self.assertEqual(result["valid_points"], 6)
        self.assertEqual(result["missing_rows"], 1)
        self.assertEqual(result["invalid_rows"], 4)
        self.assertEqual(result["uncertainty_missing_rows"], 2)
        self.assertEqual(result["uncertainty_invalid_rows"], 2)
        self.assertEqual(result["points"][0]["y_text"], "3.200")
        self.assertEqual(result["points"][0]["uncertainty"], 0.05)
        self.assertIsNone(result["points"][3]["uncertainty"])
        self.assertEqual(result["points"][7]["x"], -1)
        self.assertIsNone(result["points"][-1]["y"], "floating underflow must not become a zero measurement")

    def test_full_series_limit_never_silently_truncates(self):
        self.assertEqual(self.project([["1", "2", "0"]] * MAX_SERIES_ROWS)["valid_points"], MAX_SERIES_ROWS)
        with self.assertRaisesRegex(SeriesPlotError, "personal_series_too_large"):
            self.project([["1", "2", "0"]] * (MAX_SERIES_ROWS + 1))

    def test_invalid_column_or_uncertainty_unit_is_rejected(self):
        for series in (
            {"name": "bad", "x_column": "unknown", "y_column": "y"},
            {"name": "bad", "x_column": "x", "y_column": "x"},
        ):
            with self.assertRaises(SeriesPlotError):
                self.project([], series=series)
        for role, unit in [("ignore", "GPa"), ("uncertainty", "MPa")]:
            with self.assertRaises(SeriesPlotError):
                self.project([], columns=[{"name": "x", "role": "independent", "unit": "dpa"},
                                          {"name": "y", "role": "dependent", "unit": "GPa"},
                                          {"name": "u", "role": role, "unit": unit}])


if __name__ == "__main__":
    unittest.main()
