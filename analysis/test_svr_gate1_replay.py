"""Офлайн-тесты чистых функций analysis/svr_gate1_replay.py (без сети)."""
import unittest

from analysis.svr_gate1_replay import (WETH, USDC, encode_path, encode_quote_call,
                                       realized_net, route_candidates, usd)


class TestEncodePath(unittest.TestCase):
    def test_single_hop(self):
        p = encode_path(["0x" + "aa" * 20, "0x" + "bb" * 20], [500])
        # token(20) + fee(3=0x0001f4) + token(20)
        self.assertEqual(p, "0x" + "aa" * 20 + "0001f4" + "bb" * 20)

    def test_multi_hop(self):
        p = encode_path(["0x" + "aa" * 20, "0x" + "bb" * 20, "0x" + "cc" * 20],
                        [500, 3000])
        self.assertEqual(p, "0x" + "aa" * 20 + "0001f4" + "bb" * 20 + "000bb8" + "cc" * 20)

    def test_fee_count_mismatch(self):
        with self.assertRaises(AssertionError):
            encode_path(["0x" + "aa" * 20, "0x" + "bb" * 20], [500, 3000])


class TestEncodeQuoteCall(unittest.TestCase):
    def test_abi_layout(self):
        path = "0x" + "aa" * 20 + "0001f4" + "bb" * 20  # 43 bytes
        call = encode_quote_call(path, 10 ** 18)
        self.assertTrue(call.startswith("0xcdca1753"))
        body = call[10:]
        # offset=0x40, amountIn, length=43, then padded path
        self.assertEqual(body[:64], "40".rjust(64, "0"))
        self.assertEqual(int(body[64:128], 16), 10 ** 18)
        self.assertEqual(int(body[128:192], 16), 43)
        # path body padded to 64-hex boundary (43 bytes = 86 hex -> pad to 128)
        self.assertTrue(body[192:].startswith("aa" * 20))
        self.assertEqual(len(body[192:]) % 64, 0)


class TestRoutes(unittest.TestCase):
    def test_direct_and_weth(self):
        r = route_candidates("0x" + "11" * 20, "0x" + "22" * 20)
        self.assertIn(["0x" + "11" * 20, "0x" + "22" * 20], r)
        self.assertIn(["0x" + "11" * 20, WETH.lower(), "0x" + "22" * 20], r)

    def test_stable_intermediary(self):
        # coll→USDC→debt должен присутствовать (дыра, ловившая WBTC→USDC→USDT)
        r = route_candidates("0x" + "11" * 20, USDC.lower())
        # debt=USDC → маршрут через USDT-хаб
        r2 = route_candidates("0x" + "11" * 20, "0x" + "22" * 20)
        self.assertIn(["0x" + "11" * 20, USDC.lower(), "0x" + "22" * 20], r2)

    def test_weth_collateral_no_selfloop(self):
        r = route_candidates(WETH, USDC.lower())
        self.assertIn([WETH.lower(), USDC.lower()], r)
        for route in r:
            self.assertEqual(len(set(route)), len(route))

    def test_no_duplicate_tokens_in_route(self):
        r = route_candidates("0x" + "33" * 20, USDC.lower())
        for route in r:
            self.assertEqual(len(set(route)), len(route))


class TestUsdAndNet(unittest.TestCase):
    def test_usd(self):
        # 2 WBTC (8 dec) @ $100k (8dec oracle) = $200k
        self.assertAlmostEqual(usd(2 * 10 ** 8, 8, 100_000 * 10 ** 8), 200_000.0)

    def test_realized_net(self):
        # своп дал 105000 USDC (6dec) @ $1; долг $100k; газ $200; бид $500
        net = realized_net(105_000 * 10 ** 6, 6, 1 * 10 ** 8,
                           debt_repaid_usd=100_000, gas_usd=200, bid_usd=500)
        self.assertAlmostEqual(net, 4_300.0)

    def test_realized_net_underwater(self):
        net = realized_net(98_000 * 10 ** 6, 6, 1 * 10 ** 8,
                           debt_repaid_usd=100_000, gas_usd=200, bid_usd=500)
        self.assertAlmostEqual(net, -2_700.0)


if __name__ == "__main__":
    unittest.main()
