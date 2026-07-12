#!/usr/bin/env python3
"""Юнит-тесты Фазы D (research/cpi_vintage_plan.md §1): парсер Cleveland-Fed-винтажей и
выбор сигнала as-of. Только stdlib/unittest. Запуск:
    python3 scripts/test_backtest_cpi_vintage.py -v
Покрывают парсер-каветы §0 плана:
  (а) MM/DD-метки: год выводится из target-месяца, январские хвосты декабрьских фреймов
      переваливают год;
  (б) метка точки «Actual» не используется как дата (значение — используется);
  (в) carry-forward: as-of позже последнего винтажа -> берётся последний винтаж;
      отбор строго «дата <= asof».
"""
import gzip, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest_cpi as bc


def make_frame(sub, labels, cpi_vals, actual_vals=None):
    """Синтетический FusionCharts-фрейм в формате фида. Значения — строки-проценты, как в фиде.
    PCE-серия добавлена с мусорным значением: парсер обязан её игнорировать."""
    n = len(labels)
    actual_vals = actual_vals or [None] * n
    def data(vals):
        return [({"value": v} if v is not None else {}) for v in vals]
    return {
        "chart": {"subcaption": sub},
        "categories": [{"category": [{"label": L} for L in labels]}],
        "dataset": [
            {"seriesname": "CPI Inflation", "data": data(cpi_vals)},
            {"seriesname": "Actual CPI Inflation", "data": data(actual_vals)},
            {"seriesname": "PCE Inflation", "data": data(["99.9"] * n)},
        ],
    }


def dump(frames, suffix=".json"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    if path.endswith(".gz"):
        with gzip.open(path, "wt") as f: json.dump(frames, f)
    else:
        with open(path, "w") as f: json.dump(frames, f)
    return path


class TestLoadClevelandVintage(unittest.TestCase):
    def tearDown(self):
        if getattr(self, "_path", None) and os.path.exists(self._path): os.remove(self._path)

    def _load(self, frames, suffix=".json"):
        self._path = dump(frames, suffix)
        return bc.load_cleveland_vintage(self._path)

    def assertPoints(self, got, want):
        self.assertEqual([d for d, _ in got], [d for d, _ in want])
        for (_, gv), (_, wv) in zip(got, want): self.assertAlmostEqual(gv, wv)

    def test_year_rollover_january_tail(self):
        # каветa (а): декабрьский target, хвост оси уходит в январь СЛЕДУЮЩЕГО года
        fr = make_frame("2025-12",
                        ["12/30", "12/31", "01/02", "01/08", "01/09"],
                        ["2.80", "2.81", "2.85", "2.90", None],
                        [None, None, None, None, "2.677"])
        vin, act = self._load([fr])
        self.assertPoints(vin["2025-12"],
                          [("2025-12-30", 0.028), ("2025-12-31", 0.0281),
                           ("2026-01-02", 0.0285), ("2026-01-08", 0.029)])
        self.assertAlmostEqual(act["2025-12"], 0.02677)

    def test_first_label_already_rolled_over(self):
        # фрейм, начинающийся сразу в январе при декабрьском target -> год target+1
        fr = make_frame("2025-12", ["01/02", "01/05"], ["2.85", "2.86"])
        vin, _ = self._load([fr])
        self.assertEqual([d for d, _ in vin["2025-12"]], ["2026-01-02", "2026-01-05"])

    def test_no_rollover_within_year(self):
        # обычный фрейм: сентябрьский target, метки 09->10 того же года; subcaption 'YYYY-M'
        fr = make_frame("2025-9", ["09/02", "10/21"], ["2.95", "2.988"])
        vin, _ = self._load([fr])
        self.assertEqual([d for d, _ in vin["2025-09"]], ["2025-09-02", "2025-10-21"])

    def test_non_date_labels_skipped_and_actual_label_unused(self):
        # каветы (а)+(б): 'CPI Jan' — не дата, пропускается; метка 'Actual'-точки не дата релиза
        fr = make_frame("2025-12",
                        ["12/30", "CPI Jan", "01/08", "01/09"],
                        ["2.80", None, "2.90", None],
                        [None, None, None, "2.677"])
        vin, act = self._load([fr])
        self.assertPoints(vin["2025-12"], [("2025-12-30", 0.028), ("2026-01-08", 0.029)])
        self.assertIn("2025-12", act)   # значение Actual извлечено, его метка нигде не дата

    def test_gz_loading(self):
        fr = make_frame("2026-5", ["05/01"], ["3.88"])
        vin, _ = self._load([fr], suffix=".json.gz")
        self.assertPoints(vin["2026-05"], [("2026-05-01", 0.0388)])

    def test_pce_series_ignored(self):
        fr = make_frame("2026-5", ["05/01"], ["3.88"])
        vin, _ = self._load([fr])
        self.assertNotAlmostEqual(vin["2026-05"][0][1], 0.999)


class TestNowcastAsofAndSigma(unittest.TestCase):
    """Отбор винтажа '<= asof' (carry-forward) и no-lookahead expanding-RMSE sigma."""
    def setUp(self):
        self._save = (dict(bc.VINTAGES), dict(bc.CF_ACTUALS), dict(bc._SIG_CACHE))
        bc.VINTAGES.clear(); bc.CF_ACTUALS.clear(); bc._SIG_CACHE.clear()
        # 6 исторических таргетов (минимум SIGMA_MIN_N) с ошибкой финального винтажа 0.001
        hist = {f"2025-{m:02d}": [(f"2025-{m:02d}-28", 0.030)] for m in range(1, 7)}
        acts = {t: 0.030 - 0.001 for t in hist}
        # целевой таргет 2025-12 с тремя винтажами, включая январский хвост
        hist["2025-12"] = [("2025-12-30", 0.028), ("2026-01-02", 0.0285), ("2026-01-08", 0.029)]
        bc.VINTAGES["YoY"] = hist
        bc.CF_ACTUALS["YoY"] = dict(acts)   # actual для 2025-12 намеренно отсутствует

    def tearDown(self):
        bc.VINTAGES.clear(); bc.VINTAGES.update(self._save[0])
        bc.CF_ACTUALS.clear(); bc.CF_ACTUALS.update(self._save[1])
        bc._SIG_CACHE.clear(); bc._SIG_CACHE.update(self._save[2])

    def test_carry_forward_after_last_vintage(self):
        # каветa (в): asof (T-3) позже последнего винтажа -> carry-forward последнего
        mu, sd = bc.nowcast({}, "YoY", "2025-12", "2026-01-10")
        self.assertAlmostEqual(mu, 0.029)
        self.assertAlmostEqual(sd, 0.001)   # RMSE шести ошибок по 0.001

    def test_asof_inclusive_selection(self):
        mu, _ = bc.nowcast({}, "YoY", "2025-12", "2026-01-02")   # дата == asof включается
        self.assertAlmostEqual(mu, 0.0285)
        mu, _ = bc.nowcast({}, "YoY", "2025-12", "2026-01-01")   # между винтажами -> предыдущий
        self.assertAlmostEqual(mu, 0.028)

    def test_asof_before_first_vintage(self):
        self.assertIsNone(bc.nowcast({}, "YoY", "2025-12", "2025-12-29"))

    def test_missing_target_month(self):
        self.assertIsNone(bc.nowcast({}, "YoY", "2024-01", "2024-02-10"))

    def test_sigma_expanding_no_lookahead(self):
        # для таргета 2025-04 доступны только релизы СТРОГО до него: 01..03 -> меньше
        # SIGMA_MIN_N=6 точек -> None (сигнал честно отсутствует, а не заниженная sigma)
        self.assertIsNone(bc.vintage_sigma("YoY", "2025-04"))
        self.assertIsNone(bc.nowcast({}, "YoY", "2025-04", "2025-05-10"))

    def test_sigma_floor(self):
        # нулевые ошибки -> RMSE 0 -> floor 0.0005
        bc.CF_ACTUALS["YoY"] = {t: 0.030 for t in bc.CF_ACTUALS["YoY"]}
        bc._SIG_CACHE.clear()
        self.assertAlmostEqual(bc.vintage_sigma("YoY", "2025-12"), bc.SIGMA_FLOOR)


if __name__ == "__main__":
    unittest.main(verbosity=2)
