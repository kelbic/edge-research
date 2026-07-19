"""Офлайн юнит-тесты гейтинга шума ePBS-монитора — run:
python3 -m unittest analysis.test_epbs_monitor_noise

Проверяем разделение материя/шум после ужесточения S5: голый content-sha спеки
(правка прозы/комментария) и прогресс девнета — шум (только лог), а смена PTC-
констант/значений — материя (TG + пересчёт H1/H2). Сеть не трогается."""
import json
import unittest

import analysis.epbs_monitor as em
import analysis.epbs_sensors as es


class TestIsNoise(unittest.TestCase):
    def test_bare_content_sha_is_noise(self):
        self.assertTrue(em.is_noise("S5.files.beacon-chain.md.sha"))

    def test_latest_devnet_progression_is_noise(self):
        self.assertTrue(em.is_noise("S7.latest_devnet.name"))
        self.assertTrue(em.is_noise("S7.latest_devnet.gloas_fork_epoch"))

    def test_ptc_value_change_is_material(self):
        self.assertFalse(em.is_noise("S5.files.beacon-chain.md.ptc_values.PTC_SIZE"))

    def test_ptc_constants_change_is_material(self):
        self.assertFalse(em.is_noise("S5.files.beacon-chain.md.ptc_constants"))


class TestConstValues(unittest.TestCase):
    def test_parses_markdown_table_values(self):
        txt = "| `PTC_SIZE` | `512` |\n| `PAYLOAD_ATTESTATION_DUE_BPS` | `7500` |"
        self.assertEqual(
            es.const_values(txt, ["PTC_SIZE", "PAYLOAD_ATTESTATION_DUE_BPS"]),
            {"PTC_SIZE": "512", "PAYLOAD_ATTESTATION_DUE_BPS": "7500"})


class TestS5TriggerGating(unittest.TestCase):
    S = {"S1": {"status": "OK", "epbs_7732_in_sfi": True},
         "S2": {"status": "OK"},
         "S3": {"status": "OK", "gloas_software_exists": True},
         "S7": {"status": "OK", "repricing_in_sfi": False}}

    def test_bare_sha_does_not_trigger(self):
        trg = es.triggers(self.S, ["  S5.files.beacon-chain.md.sha:"])
        self.assertFalse(any("S5 СИГНАЛ" in t for t in trg))

    def test_ptc_value_change_triggers(self):
        trg = es.triggers(self.S, ["  S5.files.beacon-chain.md.ptc_values.PTC_SIZE:"])
        self.assertTrue(any("S5 СИГНАЛ" in t for t in trg))

    def test_schema_growth_line_does_not_trigger(self):
        trg = es.triggers(self.S, ["  S5.files.beacon-chain.md.ptc_values.PTC_SIZE: <нет> -> "
                                   "uint64(2**9)` [новое поле сенсора, не изменение источника]"])
        self.assertFalse(any("S5 СИГНАЛ" in t for t in trg))


class TestSchemaGrowth(unittest.TestCase):
    """19.07: добавление поля `ptc_values` выстрелило S5-триггером «PTC-константы изменились»,
    хотя в спеке ничего не менялось — значения (512 / 7500) совпали с зафиксированными в карте
    перехода 05.07. Ложный триггер обесценивает алертинг, поэтому рост схемы отделён от материи."""

    # ровно форма снапшотов 13.07 (ptc_values ещё null) и 19.07 (поле заполнено)
    PREV = {"sensors": {"S5": {"files": {"beacon-chain.md": {
        "sha": "aaaa", "ptc_constants": ["PTC_SIZE"], "ptc_values": None}}}}}
    CUR = {"sensors": {"S5": {"files": {"beacon-chain.md": {
        "sha": "aaaa", "ptc_constants": ["PTC_SIZE"],
        "ptc_values": {"PTC_SIZE": "uint64(2**9)` (= 512)"}}}}}}

    def test_first_population_is_schema_growth(self):
        grown = em.schema_growth_keys(self.PREV, self.CUR)
        self.assertIn("S5.files.beacon-chain.md.ptc_values.PTC_SIZE", grown)
        # сам пустой лист `ptc_values` (None -> отсутствует) в изменения не попадает вовсе:
        # flatten даёт None с обеих сторон. Ровно поэтому в нотификации 19.07 были только
        # строки по константам — фиксируем это как контракт, а не как случайность.
        self.assertNotIn("S5.files.beacon-chain.md.ptc_values",
                         em.changed_keys(self.PREV, self.CUR))

    def test_schema_growth_fires_no_trigger_end_to_end(self):
        changed = em.changed_keys(self.PREV, self.CUR)
        grown = em.schema_growth_keys(self.PREV, self.CUR)
        material = [k for k in changed if not em.is_noise(k) and k not in grown]
        trg = es.triggers(TestS5TriggerGating.S,
                          [f"  {k}:" for k in changed if k not in grown])
        self.assertEqual(material, [])
        self.assertFalse(any("S5 СИГНАЛ" in t for t in trg))

    def test_real_value_change_survives_the_gate(self):
        """Защита от передавливания: смена УЖЕ собираемого значения обязана остаться материей."""
        cur2 = json.loads(json.dumps(self.CUR))
        prev2 = json.loads(json.dumps(self.CUR))
        cur2["sensors"]["S5"]["files"]["beacon-chain.md"]["ptc_values"]["PTC_SIZE"] = "uint64(2**8)"
        grown = em.schema_growth_keys(prev2, cur2)
        changed = em.changed_keys(prev2, cur2)
        self.assertEqual(grown, set())
        trg = es.triggers(TestS5TriggerGating.S, [f"  {k}:" for k in changed])
        self.assertTrue(any("S5 СИГНАЛ" in t for t in trg))

    def test_new_constant_appearing_in_existing_map_is_material(self):
        """Если поле уже собиралось и в нём ПОЯВИЛАСЬ новая константа — это спека, не схема."""
        cur2 = json.loads(json.dumps(self.CUR))
        cur2["sensors"]["S5"]["files"]["beacon-chain.md"]["ptc_values"]["PTC_PENALTY"] = "1"
        grown = em.schema_growth_keys(self.CUR, cur2)
        self.assertEqual(grown, set())

    def test_diff_lines_mark_growth_and_leave_real_changes_clean(self):
        lines = es.diff_snapshots(self.PREV, self.CUR)
        self.assertTrue(all("новое поле сенсора" in ln for ln in lines), lines)


if __name__ == "__main__":
    unittest.main()
