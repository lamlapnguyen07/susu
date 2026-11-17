import unittest

from tests._stub_db import app


class PayMethodTextTest(unittest.TestCase):
    def test_cash_text(self):
        self.assertEqual(app._PAY_METHOD_TEXT['cash'], '取貨付現')

    def test_transfer_text(self):
        self.assertEqual(app._PAY_METHOD_TEXT['transfer'], '轉帳')


class PayReminderTest(unittest.TestCase):
    """尾句「前來取貨{pay_reminder}。」必須跟著付款方式走。"""

    def test_cash_reminder_mentions_cash(self):
        self.assertEqual(app._PAY_REMINDER['cash'], '並付現金')

    def test_transfer_reminder_is_empty(self):
        self.assertEqual(app._PAY_REMINDER['transfer'], '')

    def test_transfer_sentence_has_no_cash_wording(self):
        sentence = f"請帶著下方的取貨 QR 條碼前來取貨{app._PAY_REMINDER['transfer']}。"
        self.assertNotIn('現金', sentence)
        self.assertEqual(sentence, '請帶著下方的取貨 QR 條碼前來取貨。')


class ArrivedPayloadTest(unittest.TestCase):
    def test_payload_carries_label_for_tamsui(self):
        payload = app._build_arrived_payload('260910123456789012', 'tamsui')
        self.assertEqual(payload['order_id'], '260910123456789012')
        self.assertEqual(payload['pickup_label'], '淡水廠　2 樓大廳')

    def test_payload_carries_label_for_qiyan(self):
        payload = app._build_arrived_payload('1', 'qiyan')
        self.assertEqual(payload['pickup_label'], '奇岩廠　1 樓大廳')

    def test_legacy_order_without_location_gets_empty_label(self):
        payload = app._build_arrived_payload('1', None)
        self.assertEqual(payload['pickup_label'], '')
        self.assertEqual(payload['order_id'], '1')


if __name__ == '__main__':
    unittest.main()
