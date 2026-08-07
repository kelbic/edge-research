"""Офлайн юнит-тесты T4 газ-модели — run: python3 -m unittest analysis.test_epbs_gas_model

Проверяемые инварианты pre-registered плана (docs/epbs_t4t5_execution_plan.md §1):
floor-биндинг на толстых calldata, headroom-правило 7976/7981, арифметика надбавок
8037, AL-правило 7981 (обе ветви), дельты паттернов, «прямой путь ~0%».
Все ожидаемые числа пересчитаны вручную из параметров EIP
[P: EIP-7976/7981/8037/2780/8038, фетч 2026-08-07].

07.08: ветвь репрайсинга (2780+8038) стала ОСНОВНОЙ (режим `sfi`, триггер S7),
июльская ветвь без неё сохранена как `sfi_partial`. Числа 8038 при перепроверке
сдвинулись — TestParamPins пиннит их к первоисточнику, чтобы следующая правка
не проехала молча (июльская модель держала cold_storage 3000 против 2100 в EIP).
"""
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


class TestParamPins(unittest.TestCase):
    """Пины на числа первоисточника [P: фетч 2026-08-07]. Ловят класс ошибки, на
    котором июльская модель разошлась с EIP: репрайсинг тронул НЕ ВСЁ, что кажется."""

    def test_8038_repriced_and_untouched(self):
        e = PARAMS["eip8038"]
        self.assertEqual(e["cold_account_access"], 3_000)   # 2600 -> 3000 (+15%)
        self.assertEqual(e["storage_write"], 10_000)        # 2800 -> 10 000 (+257%)
        # НЕ репрайсятся (в таблице EIP-8038 стоит +0%) — сюда июльская модель и уехала
        self.assertEqual(e["cold_storage_access"], 2_100)
        self.assertEqual(e["warm_access"], 100)
        # AL-цены: 2400->2900, 1900->2000 (а не 3000/3000, как держал июль)
        self.assertEqual(e["al_address_cost"], 2_900)
        self.assertEqual(e["al_key_cost"], 2_000)

    def test_cold_reads_cost_the_same_after_fork(self):
        """Несущий вывод пересчёта: холодное ЧТЕНИЕ слота не дорожает."""
        self.assertEqual(regime_params(PARAMS, "sfi")["cold_storage"],
                         regime_params(PARAMS, "today")["cold_storage"])


class TestFloor7976(unittest.TestCase):
    def test_uniform_64_per_byte(self):
        f = floor_7976(20 * 1024, PARAMS)
        self.assertEqual(f["floor_per_byte"], 64)             # 16 tokens/байт * 4
        self.assertEqual(f["floor_gas"], 64 * 20480)          # 1 310 720

    def test_headroom_min_gas_limit(self):
        # headroom: лимит обязан покрывать intrinsic + floor, не фактический gasUsed.
        # В основной ветви база floor-формулы — декомпозированный интринсик 2780.
        self.assertEqual(floor_7976(20 * 1024, PARAMS)["min_gas_limit"],
                         15_000 + 1_310_720)
        self.assertEqual(floor_7976(20 * 1024, PARAMS, "sfi_partial")["min_gas_limit"],
                         21_000 + 1_310_720)

    def test_floor_binds_on_fat_calldata(self):
        c = tx_cost(PAT["swap_20KiB"], PARAMS, "sfi")
        self.assertTrue(c["floor_binds"])
        # total = интринсик 2780 + floor (exec+std ниже floor)
        self.assertEqual(c["total"], 15_000 + 64 * 20 * 1024)   # 1 325 720
        self.assertEqual(c["min_gas_limit"], 15_000 + 1_310_720)

    def test_floor_not_binding_5kib_at_300k_exec(self):
        c = tx_cost(PAT["swap_5KiB"], PARAMS, "sfi")
        self.assertFalse(c["floor_binds"])                    # 395 400 > 327 680
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
    def test_main_branch_penalties(self):
        al = al_rule_7981(PARAMS, "sfi")
        self.assertEqual(al["key_listing_cost"], 2_000 + 64 * 32)        # 4048
        self.assertEqual(al["key_listing_vs_cold"], 1_948)               # 4048 - 2100
        self.assertEqual(al["key_net_penalty"], 2_048)                   # + остаточный warm
        self.assertEqual(al["addr_listing_cost"], 2_900 + 64 * 20)       # 4180
        self.assertEqual(al["addr_net_penalty"], 1_280)
        self.assertEqual(al["verdict"], "drop_al")

    def test_partial_branch_penalties(self):
        # историческая ветвь: 2930-цены + data-надбавка 7981 (числа плана §1)
        al = al_rule_7981(PARAMS, "sfi_partial")
        self.assertEqual(al["key_listing_cost"], 1_900 + 64 * 32)        # 3948
        self.assertEqual(al["key_listing_vs_cold"], 1_848)               # число плана
        self.assertEqual(al["key_net_penalty"], 1_948)
        self.assertEqual(al["addr_net_penalty"], 1_180)
        self.assertEqual(al["verdict"], "drop_al")

    def test_today_al_still_saves(self):
        # до форка AL экономит 100/ключ — правило «дроп» именно про SFI+
        al = al_rule_7981(PARAMS, "today")
        self.assertEqual(al["key_net_penalty"], -100)
        self.assertEqual(al["verdict"], "keep_al")


class TestPatternDeltas(unittest.TestCase):
    def test_direct_path_zero_in_partial_branch(self):
        # инвариант Gate 0 «прямой путь ~0%» — свойство ИМЕННО partial-ветви
        for name in ("liq_direct_800B", "swap_800B", "swap_5KiB"):
            d = deltas(PAT[name], PARAMS)
            self.assertAlmostEqual(d["d_partial_pct"], 0.0, delta=0.5, msg=name)

    def test_direct_path_no_longer_zero_in_main_branch(self):
        # ...и он НЕ переносится на основную ветвь: 8038 бьёт по exec, не по calldata
        for name in ("liq_direct_800B", "swap_800B", "swap_5KiB"):
            d = deltas(PAT[name], PARAMS)
            self.assertGreater(d["d_sfi_pct"], 10.0, msg=name)

    def test_20kib_anchor_and_150pct(self):
        d = deltas(PAT["swap_20KiB"], PARAMS)
        self.assertEqual(d["today"], 533_000)                 # якорь Gate 0
        self.assertEqual(d["sfi_partial"], 1_331_720)
        self.assertTrue(145.0 < d["d_partial_pct"] < 155.0)   # ~ +150%
        # в основной ветви floor тот же, интринсик ниже на 6000 (2780)
        self.assertEqual(d["sfi"], 15_000 + 1_310_720)
        self.assertTrue(145.0 < d["d_sfi_pct"] < 155.0)

    def test_new_slot_pattern_costs_full_slot_price(self):
        d_base = deltas(PAT["liq_direct_800B"], PARAMS)
        d_slot = deltas(PAT["liq_new_token_slot"], PARAMS)
        # разница паттернов = полная цена нового слота в основной ветви
        self.assertEqual(d_slot["sfi"] - d_base["sfi"],
                         2_100 + 10_000 + 97_920)             # 110 020
        # в partial против today того же паттерна — ровно дельта 8037 на слот
        self.assertEqual(d_slot["sfi_partial"] - d_slot["today"], 77_920)
        # в основной ветви к ней добавляются STORAGE_WRITE и репрайсинг профиля,
        # минус подешевевший интринсик 2780
        self.assertEqual(d_slot["sfi"] - d_slot["today"],
                         77_920 + 10_000 + 74_200 - 6_000)    # 156 120

    def test_repricing_positive_on_direct_path(self):
        # 8038 дорожит стейт-доступ: основная ветвь > today на прямом пути
        d = deltas(PAT["liq_direct_800B"], PARAMS)
        self.assertGreater(d["d_sfi_pct"], 10.0)
        # интринсик 2780: вызов контракта = 15 000
        self.assertEqual(tx_cost(PAT["liq_direct_800B"], PARAMS, "sfi")["intrinsic"], 15_000)

    def test_liq_profile_repricing_is_74200(self):
        """Несущее число пересчёта 07.08 (июль давал +105 700 из-за cold_storage 3000).

        Профиль: 8 холодных аккаунтов, 25 холодных слотов, 10 записей."""
        t = regime_params(PARAMS, "today")
        s = regime_params(PARAMS, "sfi")
        today_cost = 8 * t["cold_account"] + 25 * t["cold_storage"] + 10 * t["sstore_update_cold"]
        sfi_cost = 8 * s["cold_account"] + 25 * s["cold_storage"] + 10 * s["sstore_update_cold"]
        self.assertEqual(sfi_cost - today_cost, 74_200)

    def test_al_pattern_costs_extra(self):
        d_al = deltas(PAT["liq_with_al"], PARAMS)
        d_no = deltas(PAT["liq_direct_800B"], PARAMS)
        # сегодня AL почти нейтрален (экономит 600 на 2а+4к)
        self.assertEqual(d_no["today"] - d_al["today"], 600)
        # хранение AL стоит ровно сумму чистых штрафов ветви
        self.assertEqual(d_al["sfi"] - d_no["sfi"], 2 * 1_280 + 4 * 2_048)        # 10 752
        self.assertEqual(d_al["sfi_partial"] - d_no["sfi_partial"],
                         2 * 1_180 + 4 * 1_948)                                   # 10 152


class TestRegimeParams(unittest.TestCase):
    def test_composite_state_op_prices(self):
        t = regime_params(PARAMS, "today")
        p = regime_params(PARAMS, "sfi_partial")
        s = regime_params(PARAMS, "sfi")
        self.assertEqual(t["sstore_new_slot_cold"], 22_100)   # 2100 + 20000
        self.assertEqual(p["sstore_new_slot_cold"], 100_020)  # 2100 + 97 920
        self.assertEqual(s["sstore_new_slot_cold"], 110_020)  # 2100 + 10000 + 97 920
        self.assertEqual(s["sstore_update_cold"], 12_100)     # 2100 + 10000 (8038)
        self.assertEqual(p["intrinsic_base"], 21_000)
        self.assertEqual(s["intrinsic_base"], 15_000)         # 2780

    def test_unknown_regime_raises(self):
        with self.assertRaises(ValueError):
            regime_params(PARAMS, "mainnet")

    def test_retired_regime_name_raises(self):
        # старое имя ветви ушло: «cfi» больше не режим, чтобы старый вызов не считал молча
        with self.assertRaises(ValueError):
            regime_params(PARAMS, "cfi")

    def test_today_regime_reproduces_exec_gas(self):
        # переоценка стейт-опов в today — тождество (exec_gas не искажается)
        c = tx_cost(PAT["liq_direct_800B"], PARAMS, "today")
        self.assertEqual(c["exec_gas"], 300_000)
        self.assertEqual(c["total"], 21_000 + 8_000 + 300_000)


if __name__ == "__main__":
    unittest.main()
