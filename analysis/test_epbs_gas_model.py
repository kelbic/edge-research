"""Офлайн юнит-тесты T4 газ-модели — run: python3 -m unittest analysis.test_epbs_gas_model

Проверяемые инварианты pre-registered плана (docs/epbs_t4t5_execution_plan.md §1):
floor-биндинг на толстых calldata, headroom-правило 7976/7981, арифметика надбавок
8037, AL-правило 7981 (обе ветви), дельты паттернов SFI/CFI, «прямой путь ~0%».
Все ожидаемые числа пересчитаны вручную из параметров EIP
[P: EIP-7976/7981/8037/2780/8038, фетч 2026-07-12]."""
import unittest

from analysis.epbs_gas_model import (
    PARAMS,
    PATTERNS,
    al_rule_7981,
    deltas,
    floor_7976,
    regime_params,
    surcharge_8037,
    tx_cost,
)

PAT = {p["name"]: p for p in PATTERNS}


class TestFloor7976(unittest.TestCase):
    def test_uniform_64_per_byte(self):
        f = floor_7976(20 * 1024, PARAMS)
        self.assertEqual(f["floor_per_byte"], 64)             # 16 tokens/байт * 4
        self.assertEqual(f["floor_gas"], 64 * 20480)          # 1 310 720

    def test_headroom_min_gas_limit(self):
        # headroom: лимит обязан покрывать intrinsic + floor, не фактический gasUsed
        f = floor_7976(20 * 1024, PARAMS)
        self.assertEqual(f["min_gas_limit"], 21_000 + 1_310_720)

    def test_floor_binds_on_fat_calldata(self):
        c = tx_cost(PAT["swap_20KiB"], PARAMS, "sfi")
        self.assertTrue(c["floor_binds"])
        # total = 21000 + floor (exec+std ниже floor)
        self.assertEqual(c["total"], 21_000 + 64 * 20 * 1024)   # 1 331 720
        self.assertEqual(c["min_gas_limit"], 21_000 + 1_310_720)

    def test_floor_not_binding_5kib_at_300k_exec(self):
        c = tx_cost(PAT["swap_5KiB"], PARAMS, "sfi")
        self.assertFalse(c["floor_binds"])                    # 351 200 > 327 680
        self.assertEqual(c["floor_gas"], 64 * 5 * 1024)


class TestSurcharge8037(unittest.TestCase):
    def test_slot_and_account_costs(self):
        s = surcharge_8037(1, 1, PARAMS)
        self.assertEqual(s["slot_cost"], 64 * 1530)           # 97 920
        self.assertEqual(s["account_cost"], 120 * 1530)       # 183 600
        self.assertAlmostEqual(s["slot_multiple"], 4.896)     # ~4.9x
        self.assertAlmostEqual(s["account_multiple"], 7.344)

    def test_delta_arithmetic(self):
        self.assertEqual(surcharge_8037(1, 0, PARAMS)["delta_vs_today"], 77_920)
        self.assertEqual(surcharge_8037(0, 1, PARAMS)["delta_vs_today"], 158_600)
        self.assertEqual(surcharge_8037(2, 1, PARAMS)["surcharge_total"],
                         2 * 97_920 + 183_600)


class TestAlRule7981(unittest.TestCase):
    def test_sfi_penalties(self):
        al = al_rule_7981(PARAMS, "sfi")
        self.assertEqual(al["key_listing_cost"], 1_900 + 64 * 32)        # 3948
        self.assertEqual(al["key_listing_vs_cold"], 1_848)               # число плана
        self.assertEqual(al["key_net_penalty"], 1_948)                   # + остаточный warm
        self.assertEqual(al["addr_listing_cost"], 2_400 + 64 * 20)       # 3680
        self.assertEqual(al["addr_net_penalty"], 1_180)
        self.assertEqual(al["verdict"], "drop_al")

    def test_cfi_penalties_worse(self):
        al = al_rule_7981(PARAMS, "cfi")
        self.assertEqual(al["key_net_penalty"], 3_000 + 2_048 + 100 - 3_000)  # 2148
        self.assertEqual(al["addr_net_penalty"], 3_000 + 1_280 + 100 - 3_000)  # 1380
        self.assertEqual(al["verdict"], "drop_al")

    def test_today_al_still_saves(self):
        # до форка AL экономит 100/ключ — правило «дроп» именно про SFI+
        al = al_rule_7981(PARAMS, "today")
        self.assertEqual(al["key_net_penalty"], -100)
        self.assertEqual(al["verdict"], "keep_al")


class TestPatternDeltas(unittest.TestCase):
    def test_direct_path_invariant_zero_sfi(self):
        # инвариант Gate 0: прямой путь ~0% в SFI-ветви
        for name in ("liq_direct_800B", "swap_800B", "swap_5KiB"):
            d = deltas(PAT[name], PARAMS)
            self.assertAlmostEqual(d["d_sfi_pct"], 0.0, delta=0.5, msg=name)

    def test_20kib_anchor_and_150pct(self):
        d = deltas(PAT["swap_20KiB"], PARAMS)
        self.assertEqual(d["today"], 533_000)                 # якорь Gate 0
        self.assertEqual(d["sfi"], 1_331_720)
        self.assertTrue(145.0 < d["d_sfi_pct"] < 155.0)       # ~ +150%
        self.assertEqual(d["cfi"], 15_000 + 1_310_720)        # floor + интринсик 2780

    def test_new_slot_pattern_plus_77920(self):
        d_base = deltas(PAT["liq_direct_800B"], PARAMS)
        d_slot = deltas(PAT["liq_new_token_slot"], PARAMS)
        self.assertEqual(d_slot["sfi"] - d_base["sfi"],
                         77_920 + 22_100)   # +97 920 нового слота против его отсутствия
        # против today того же паттерна: ровно дельта 8037 на слот
        self.assertEqual(d_slot["sfi"] - d_slot["today"], 77_920)

    def test_cfi_repricing_positive_on_direct_path(self):
        # 8038 дорожит стейт-доступ: CFI-ветвь > today на прямом пути
        d = deltas(PAT["liq_direct_800B"], PARAMS)
        self.assertGreater(d["d_cfi_pct"], 10.0)
        # интринсик 2780: вызов контракта = 15 000
        self.assertEqual(tx_cost(PAT["liq_direct_800B"], PARAMS, "cfi")["intrinsic"], 15_000)

    def test_al_pattern_costs_extra_in_sfi(self):
        d_al = deltas(PAT["liq_with_al"], PARAMS)
        d_no = deltas(PAT["liq_direct_800B"], PARAMS)
        # сегодня AL почти нейтрален (экономит 600 на 2а+4к)
        self.assertEqual(d_no["today"] - d_al["today"], 600)
        # в SFI хранение AL стоит ровно сумму чистых штрафов: 2*1180 + 4*1948
        self.assertEqual(d_al["sfi"] - d_no["sfi"], 2 * 1_180 + 4 * 1_948)


class TestRegimeParams(unittest.TestCase):
    def test_composite_state_op_prices(self):
        t = regime_params(PARAMS, "today")
        s = regime_params(PARAMS, "sfi")
        c = regime_params(PARAMS, "cfi")
        self.assertEqual(t["sstore_new_slot_cold"], 22_100)   # 2100 + 20000
        self.assertEqual(s["sstore_new_slot_cold"], 100_020)  # 2100 + 97 920
        self.assertEqual(c["sstore_new_slot_cold"], 110_920)  # 3000 + 10000 + 97 920
        self.assertEqual(c["sstore_update_cold"], 13_000)     # 3000 + 10000 (8038)
        self.assertEqual(s["intrinsic_base"], 21_000)
        self.assertEqual(c["intrinsic_base"], 15_000)         # 2780

    def test_unknown_regime_raises(self):
        with self.assertRaises(ValueError):
            regime_params(PARAMS, "mainnet")

    def test_today_regime_reproduces_exec_gas(self):
        # переоценка стейт-опов в today — тождество (exec_gas не искажается)
        c = tx_cost(PAT["liq_direct_800B"], PARAMS, "today")
        self.assertEqual(c["exec_gas"], 300_000)
        self.assertEqual(c["total"], 21_000 + 8_000 + 300_000)


if __name__ == "__main__":
    unittest.main()
