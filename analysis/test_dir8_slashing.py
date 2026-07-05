"""Офлайн-тесты чистых функций analysis/dir8_slashing.py (без сети)."""
import unittest

from analysis.dir8_slashing import (TOPICS, classify_event,
                                    decode_operator_slashed, month_key)


def w(x, width=64):
    return (hex(x)[2:] if isinstance(x, int) else x).rjust(width, "0")


def build_event(operator="0x" + "aa" * 20, avs="0x" + "bb" * 20, set_id=3,
                strategies=1, wads=(0.5,), desc="test"):
    """Собирает ABI-энкод data для OperatorSlashed (все поля не-indexed):
    head: operator, avs, set_id, off_strat, off_wad, off_desc; затем хвосты."""
    head_words = 6
    strat_tail = [w(strategies)] + [w("11" * 20, 64) for _ in range(strategies)]
    off_strat = head_words * 32
    wad_tail = [w(len(wads))] + [w(int(v * 1e18)) for v in wads]
    off_wad = off_strat + len(strat_tail) * 32
    db = desc.encode()
    desc_tail = [w(len(db))] + [(db.hex() + "0" * 64)[:64]] if db else [w(0)]
    off_desc = off_wad + len(wad_tail) * 32
    words = [w(operator[2:]), w(avs[2:]), w(set_id),
             w(off_strat), w(off_wad), w(off_desc)] + strat_tail + wad_tail + desc_tail
    return "0x" + "".join(words)


class TestTopics(unittest.TestCase):
    def test_operator_slashed_topic0(self):
        # сверено с живым логом mainnet (скан 2026-07-05)
        self.assertEqual(TOPICS["OperatorSlashed"],
                         "0x80969ad29428d6797ee7aad084f9e4a42a82fc506dcd2ca3b6fb431f85ccebe5")


class TestDecode(unittest.TestCase):
    def test_roundtrip(self):
        d = decode_operator_slashed(build_event(set_id=7, wads=(0.25, 1.0),
                                                strategies=2, desc="slash 10%"))
        self.assertEqual(d["operator_set_id"], 7)
        self.assertEqual(d["n_strategies"], 2)
        self.assertAlmostEqual(d["wads"][0], 0.25)
        self.assertAlmostEqual(d["wads"][1], 1.0)
        self.assertEqual(d["description"], "slash 10%")
        self.assertEqual(d["operator"], "0x" + "aa" * 20)
        self.assertEqual(d["avs"], "0x" + "bb" * 20)

    def test_empty_description(self):
        d = decode_operator_slashed(build_event(desc=""))
        self.assertEqual(d["description"], "")


class TestClassify(unittest.TestCase):
    def test_test_event(self):
        self.assertEqual(classify_event("slash 10%", [0.1]), "test")

    def test_promo_spam(self):
        self.assertEqual(classify_event("👉 eigenyields.xyz/vaults", [1.0]), "promo-spam")

    def test_url_partial_slash_is_other(self):
        # URL, но слэш не 100% — не уверены, что спам
        self.assertEqual(classify_event("see example.xyz", [0.5]), "other")

    def test_empty(self):
        self.assertEqual(classify_event("", [1.0]), "no-description")

    def test_real_looking(self):
        self.assertEqual(classify_event("AlephAVS redistributable slash", [1.0]), "other")


class TestMonthKey(unittest.TestCase):
    def test_month(self):
        self.assertEqual(month_key(1751728384), "2025-07")


if __name__ == "__main__":
    unittest.main()
