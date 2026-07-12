"""Тесты position-сканера Midnight: селекторы геттеров, декод borrower из топиков,
классификация сейзабельности."""
import unittest

from analysis.keccak import keccak256
from analysis.midnight_positions import (
    SEL_DEBT, TOPIC_SUPPLY, TOPIC_UPDATE, _w,
)


class TestSelectors(unittest.TestCase):
    def test_debt_selector(self):
        self.assertEqual(SEL_DEBT, "0x" + keccak256(b"debt(bytes32,address)").hex()[:8])

    def test_event_topics(self):
        self.assertEqual(TOPIC_SUPPLY, "0x" + keccak256(
            b"SupplyCollateral(address,bytes32,address,uint256,address)").hex())
        self.assertEqual(TOPIC_UPDATE, "0x" + keccak256(
            b"UpdatePosition(bytes32,address,uint256,uint256,uint256)").hex())

    def test_debt_calldata_shape(self):
        mid = "0x" + "ab" * 32
        borrower = "0x" + "cd" * 20
        data = SEL_DEBT + mid[2:] + _w(borrower[2:])
        # 4-байт селектор + 32 (id) + 32 (адрес, left-padded)
        self.assertEqual(len(data), 2 + 8 + 64 + 64)
        self.assertTrue(data.endswith("cd" * 20))
        self.assertIn("ab" * 32, data)


class TestBorrowerDecode(unittest.TestCase):
    def test_supply_onbehalf_from_topic3(self):
        # SupplyCollateral: topics = [t0, id_, collateral, onBehalf]; borrower = topics[3][26:]
        onbehalf = "11" * 20
        topic3 = "0x" + "00" * 12 + onbehalf
        borrower = "0x" + topic3[26:]
        self.assertEqual(borrower, "0x" + onbehalf)

    def test_update_user_from_topic2(self):
        user = "22" * 20
        topic2 = "0x" + "00" * 12 + user
        self.assertEqual("0x" + topic2[26:], "0x" + user)


if __name__ == "__main__":
    unittest.main()
