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


class TestNotationOnly(unittest.TestCase):
    """25.07: апстрим прогнал по consensus-specs два репо-широких переименования типов
    (PR #5469 `uint*`->`Uint*`, PR #5466 `boolean`->`Boolean`, оба 22.07). Текст каждой
    константы сдвинулся, ни одно число не изменилось — и S5 выстрелил «PTC-константы
    изменились» в третий раз подряд вхолостую. Проверено побайтово: после снятия регистра
    beacon-chain.md и validator.md сводятся к копиям от 19.07 без остатка."""

    # ровно значения из state 19.07 -> 25.07
    PREV = {"sensors": {"S5": {"files": {
        "beacon-chain.md": {"sha": "ed766a2b4313e61e",
                            "ptc_values": {"PTC_SIZE": "uint64(2**9)` (= 512)"}},
        "validator.md": {"sha": "1d17c05aeba37d3e",
                         "ptc_values": {"PAYLOAD_ATTESTATION_DUE_BPS": "uint64(7500)"}}}}}}
    CUR = {"sensors": {"S5": {"files": {
        "beacon-chain.md": {"sha": "5a5de9b01f69279f",
                            "ptc_values": {"PTC_SIZE": "Uint64(2**9)` (= 512)"}},
        "validator.md": {"sha": "4f27b4ad2be4b878",
                         "ptc_values": {"PAYLOAD_ATTESTATION_DUE_BPS": "Uint64(7500)"}}}}}}

    def test_case_rename_fires_no_trigger_end_to_end(self):
        changed = em.changed_keys(self.PREV, self.CUR)
        inert = em.schema_growth_keys(self.PREV, self.CUR) \
            | em.notation_only_keys(self.PREV, self.CUR)
        material = [k for k in changed if not em.is_noise(k) and k not in inert]
        trg = es.triggers(TestS5TriggerGating.S,
                          [f"  {k}:" for k in changed if k not in inert])
        self.assertEqual(material, [])           # sha — шум, константы — нотация
        self.assertFalse(any("S5 СИГНАЛ" in t for t in trg))

    def test_both_ptc_paths_are_classified_as_notation(self):
        notation = em.notation_only_keys(self.PREV, self.CUR)
        self.assertEqual(notation, {
            "S5.files.beacon-chain.md.ptc_values.PTC_SIZE",
            "S5.files.validator.md.ptc_values.PAYLOAD_ATTESTATION_DUE_BPS"})
        # sha менялся по-настоящему -> нотацией он НЕ является (его гасит is_noise, не этот гейт)
        self.assertNotIn("S5.files.beacon-chain.md.sha", notation)

    def test_value_change_under_the_same_rename_survives(self):
        """Главная защита от передавливания: если вместе с регистром поехало ЧИСЛО — материя."""
        cur2 = json.loads(json.dumps(self.CUR))
        cur2["sensors"]["S5"]["files"]["beacon-chain.md"]["ptc_values"]["PTC_SIZE"] = \
            "Uint64(2**10)` (= 1024)"
        notation = em.notation_only_keys(self.PREV, cur2)
        self.assertNotIn("S5.files.beacon-chain.md.ptc_values.PTC_SIZE", notation)
        changed = em.changed_keys(self.PREV, cur2)
        trg = es.triggers(TestS5TriggerGating.S,
                          [f"  {k}:" for k in changed if k not in notation])
        self.assertTrue(any("S5 СИГНАЛ" in t for t in trg))

    def test_type_width_change_is_not_notation(self):
        """uint64 -> uint256 отличается не только регистром: ширина типа остаётся материей."""
        fp = {"x": "uint64(7500)"}
        self.assertFalse(es.is_notation_only(fp, {"x": "uint256(7500)"}, "x"))
        self.assertTrue(es.is_notation_only(fp, {"x": "Uint64(7500)"}, "x"))

    def test_appearing_path_is_not_notation(self):
        self.assertFalse(es.is_notation_only({}, {"x": "Uint64(1)"}, "x"))
        self.assertFalse(es.is_notation_only({"x": "uint64(1)"}, {}, "x"))

    def test_diff_lines_mark_notation(self):
        lines = [ln for ln in es.diff_snapshots(self.PREV, self.CUR) if "ptc_values" in ln]
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(all(es.NOTATION_MARK in ln for ln in lines), lines)


class TestS6TimingGate(unittest.TestCase):
    """S6-сигнал раньше считался ДО фильтра меток и был открыт тому же классу ложных
    срабатываний, что дважды поймал S5. Фиксируем, что оба сигнала теперь за одним гейтом."""

    def test_notation_line_does_not_trigger_s6(self):
        trg = es.triggers(TestS5TriggerGating.S,
                          [f"  S6.timing.SLOT_DURATION_MS: uint64(12000) -> "
                           f"Uint64(12000) {es.NOTATION_MARK}"])
        self.assertFalse(any("S6 СИГНАЛ" in t for t in trg))

    def test_real_timing_change_still_triggers_s6(self):
        trg = es.triggers(TestS5TriggerGating.S,
                          ["  S6.timing.SLOT_DURATION_MS: 12000 -> 6000"])
        self.assertTrue(any("S6 СИГНАЛ" in t for t in trg))


class TestTriggerLatch(unittest.TestCase):
    """07.08: S7 сработал по-настоящему (репрайсинг вошёл в SFI). Триггер сформулирован
    как СОСТОЯНИЕ, а не переход, поэтому без защёлки он звонил бы в TG каждые 3 дня
    вечно. Защёлка: один звонок на состояние; смена состояния (эскалация) — новый звонок."""

    S = {
        "S1": {"status": "OK", "epbs_7732_in_sfi": True},
        "S2": {"status": "OK", "fork_epoch": {"mainnet": es.UNSET_EPOCH},
               "activation_rows_filled": {"Sepolia": False}, "epoch_set_somewhere": False},
        "S3": {"status": "OK", "gloas_software_exists": False},
        "S7": {"status": "OK", "repricing_in_sfi": True,
               "cfi_watch": {"2780": "SFI", "8038": "SFI", "7904": "absent"}},
    }

    def test_first_time_trigger_goes_to_tg(self):
        trg = es.triggers(self.S, [])
        new, repeat = em.split_triggers(trg, em.trigger_fingerprints(self.S), {})
        self.assertTrue(any("S7 ТРИГГЕР" in t for t in new))
        self.assertEqual(repeat, [])

    def test_same_state_next_run_is_silent(self):
        fps = em.trigger_fingerprints(self.S)
        trg = es.triggers(self.S, [])
        new, repeat = em.split_triggers(trg, fps, {"S7": fps["S7"]})
        self.assertEqual(new, [])                      # TG молчит
        self.assertTrue(any("S7 ТРИГГЕР" in t for t in repeat))   # но тревога в силе

    def test_escalation_rearms(self):
        fps_old = em.trigger_fingerprints(self.S)
        esc = json.loads(json.dumps(self.S))           # третий EIP вошёл в SFI
        esc["S7"]["cfi_watch"]["7904"] = "SFI"
        new, repeat = em.split_triggers(es.triggers(esc, []),
                                        em.trigger_fingerprints(esc),
                                        {"S7": fps_old["S7"]})
        self.assertTrue(any("S7 ТРИГГЕР" in t for t in new))
        self.assertEqual(repeat, [])

    def test_unavailable_source_does_not_rearm(self):
        """Просвет — не ре-арм: недоступный источник восстанавливается carry-forward,
        отпечаток тот же, повторного звонка нет."""
        fps = em.trigger_fingerprints(self.S)
        prev = {"sensors": self.S, "trigger_latch": {"S7": fps["S7"]}}
        cur = {"date": "2026-08-10",
               "sensors": dict(self.S, S7={"status": "UNAVAILABLE"})}
        cur = em.carry_forward(cur, prev)
        new, repeat = em.split_triggers(es.triggers(cur["sensors"], []),
                                        em.trigger_fingerprints(cur["sensors"]),
                                        prev["trigger_latch"])
        self.assertEqual(new, [])
        self.assertEqual(len(repeat), 1)

    def test_s2_epoch_latches_and_rearms_on_move(self):
        """Именно здесь усталость от алертов дороже всего: после установки эпохи
        S2 звонил бы каждые 3 дня до самого форка."""
        s = json.loads(json.dumps(self.S))
        s["S2"]["fork_epoch"]["sepolia"] = 700_000
        s["S2"]["epoch_set_somewhere"] = True
        fps = em.trigger_fingerprints(s)
        new, _ = em.split_triggers(es.triggers(s, []), fps, {})
        self.assertTrue(any("S2 ТРИГГЕР" in t for t in new))
        latch = {k: fps[k] for k in ("S2", "S3", "S7") if k in fps}
        new2, repeat2 = em.split_triggers(es.triggers(s, []), fps, latch)
        self.assertEqual(new2, [])
        self.assertEqual(len(repeat2), 3)              # S2 + S3 + S7 — все в логе
        moved = json.loads(json.dumps(s))              # эпоху сдвинули -> звонить снова
        moved["S2"]["fork_epoch"]["sepolia"] = 701_000
        new3, _ = em.split_triggers(es.triggers(moved, []),
                                    em.trigger_fingerprints(moved), latch)
        self.assertTrue(any("S2 ТРИГГЕР" in t for t in new3))

    def test_s2_fingerprint_survives_partial_fetch_failure(self):
        """Флап ОДНОГО config-URL не должен ре-армить S2.

        `sensor_s2` пишет per-network "UNAVAILABLE" при сетевой ошибке, оставляя
        статус сенсора OK — carry_forward такой снимок не чинит (он подменяет
        только сенсор целиком). Отпечаток по сырому снимку дал бы ложный повторный
        звонок на каждой икоте; отпечаток по сигналу — нет."""
        s = json.loads(json.dumps(self.S))
        s["S2"]["fork_epoch"]["sepolia"] = 700_000
        s["S2"]["epoch_set_somewhere"] = True
        fps = em.trigger_fingerprints(s)
        latch = {k: fps[k] for k in fps}
        wobble = json.loads(json.dumps(s))              # holesky-URL отвалился
        wobble["S2"]["fork_epoch"]["holesky"] = "UNAVAILABLE"
        wobble["S2"]["activation_rows_filled"] = {}     # S1 лежал при сборке
        new, repeat = em.split_triggers(es.triggers(wobble, []),
                                        em.trigger_fingerprints(wobble), latch)
        self.assertEqual(new, [])                       # икота не будит
        self.assertTrue(any("S2 ТРИГГЕР" in t for t in repeat))
        # а вот заполнившаяся строка активации — настоящая новость, ре-арм
        filled = json.loads(json.dumps(s))
        filled["S2"]["activation_rows_filled"]["Sepolia"] = True
        new2, _ = em.split_triggers(es.triggers(filled, []),
                                    em.trigger_fingerprints(filled), latch)
        self.assertTrue(any("S2 ТРИГГЕР" in t for t in new2))

    def test_change_driven_signals_are_never_latched(self):
        """S5/S6 поднимаются только по изменившимся ключам — они уже переходы,
        защёлка их не касается (иначе реальный второй сдвиг тайминга пропал бы)."""
        trg = es.triggers(self.S, ["  S6.timing.SLOT_DURATION_MS: 12000 -> 6000"])
        fps = em.trigger_fingerprints(self.S)
        new, _ = em.split_triggers(trg, fps, {k: v for k, v in fps.items()})
        self.assertTrue(any("S6 СИГНАЛ" in t for t in new))


if __name__ == "__main__":
    unittest.main()
