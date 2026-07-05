"""Офлайн-тесты чистых функций analysis/oev_svr_base.py (без сети)."""
import unittest

from analysis.oev_svr_base import (TOPIC_SOLVER, TOPIC_UPD, classify_receipt,
                                   solver_fields)


def w(x, width=64):
    return (hex(x)[2:] if isinstance(x, int) else x).rjust(width, "0")


# реальная data из живой tx 0xd7f75480…a85dd0 (Base, блок 48039517):
# bidToken=0, bidAmount=0x23228a6dc5954c, executed=1, success=1
REAL_DATA = ("0x" + w(0) + w(0x23228a6dc5954c) + w(1) + w(1) + w(0))
SOLVER = "0xd12810b19b596347a3afac206d3ca65d08594b3f"


def solver_log(data=REAL_DATA):
    return {"topics": [TOPIC_SOLVER, "0x" + w(SOLVER[2:]), "0x" + w(0), "0x" + w(0)],
            "data": data}


def upd_log():
    return {"topics": [TOPIC_UPD], "data": "0x"}


class TestSolverFields(unittest.TestCase):
    def test_real_data(self):
        f = solver_fields(REAL_DATA)
        self.assertEqual(f["bidAmount"], 0x23228a6dc5954c)  # ≈0.00988 ETH
        self.assertEqual(f["bidToken"], "0x" + "0" * 40)
        self.assertTrue(f["executed"])
        self.assertTrue(f["success"])


class TestClassify(unittest.TestCase):
    def test_svr_with_winner(self):
        c = classify_receipt([upd_log(), solver_log()])
        self.assertTrue(c["is_svr"])
        self.assertEqual(c["solver"], SOLVER)
        self.assertEqual(c["bid_wei"], 0x23228a6dc5954c)

    def test_non_svr(self):
        c = classify_receipt([solver_log()])
        self.assertFalse(c["is_svr"])

    def test_failed_solver_ignored(self):
        bad = ("0x" + w(0) + w(123) + w(1) + w(0) + w(0))  # success=False
        c = classify_receipt([upd_log(), solver_log(bad)])
        self.assertTrue(c["is_svr"])
        self.assertIsNone(c["solver"])
        self.assertIsNone(c["bid_wei"])


if __name__ == "__main__":
    unittest.main()
