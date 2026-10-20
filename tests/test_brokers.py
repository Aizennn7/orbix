from __future__ import annotations

import unittest

from orbix.brokers import capabilities, pocket_option_ticket


def signal(side: str = "BUY") -> dict:
    return {
        "pair": "EUR/USD",
        "side": side,
        "horizon": 1,
        "entry_time": "2024-03-28T06:07:00Z",
        "prediction_time": "2024-03-28T06:08:00Z",
    }


class BrokerHandoffTests(unittest.TestCase):
    def test_capabilities_never_claim_order_execution(self) -> None:
        caps = capabilities()
        self.assertEqual(caps["name"], "Pocket Option")
        self.assertFalse(caps["official_api"])
        self.assertFalse(caps["order_execution"])
        self.assertFalse(caps["credentials_required"])
        self.assertTrue(caps["manual_handoff"])
        self.assertIn("no official public trading API", caps["note"])

    def test_buy_is_call_and_sell_is_put(self) -> None:
        buy = pocket_option_ticket(signal("BUY"))
        sell = pocket_option_ticket(signal("SELL"))
        self.assertEqual(buy["direction"], "CALL")
        self.assertEqual(sell["direction"], "PUT")
        self.assertEqual(buy["asset"], "EURUSD")
        self.assertEqual(buy["expiry_minutes"], 1)
        self.assertFalse(buy["orders_sent"])
        self.assertEqual(buy["mode"], "MANUAL")

    def test_no_signal_has_no_ticket(self) -> None:
        self.assertIsNone(pocket_option_ticket(signal("NO SIGNAL")))
        self.assertIsNone(pocket_option_ticket({}))

    def test_ticket_text_is_explicit_about_manual_entry(self) -> None:
        ticket = pocket_option_ticket(signal("BUY"))
        self.assertIn("ORBIX · EUR/USD · CALL", ticket["text"])
        self.assertIn("Entry 2024-03-28T06:07:00Z", ticket["text"])
        self.assertIn("Expiry 2024-03-28T06:08:00Z", ticket["text"])
        self.assertIn("No order was sent", ticket["text"])
        self.assertNotIn("ssid", ticket["text"].lower())
        self.assertNotIn("password", ticket["text"].lower())


if __name__ == "__main__":
    unittest.main()
