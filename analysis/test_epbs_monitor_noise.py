"""Офлайн юнит-тесты гейтинга шума ePBS-монитора — run:
python3 -m unittest analysis.test_epbs_monitor_noise

Проверяем разделение материя/шум после ужесточения S5: голый content-sha спеки
(правка прозы/комментария) и прогресс девнета — шум (только лог), а смена PTC-
констант/значений — материя (TG + пересчёт H1/H2). Сеть не трогается."""
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


if __name__ == "__main__":
    unittest.main()
