import unittest

from tests._stub_db import app


class PickupLabelTest(unittest.TestCase):
    def test_tamsui_is_second_floor_lobby(self):
        self.assertEqual(app.pickup_label('tamsui'), '淡水廠　2 樓大廳')

    def test_qiyan_is_first_floor_lobby(self):
        self.assertEqual(app.pickup_label('qiyan'), '奇岩廠　1 樓大廳')

    def test_none_returns_empty_string(self):
        self.assertEqual(app.pickup_label(None), '')

    def test_empty_string_returns_empty_string(self):
        self.assertEqual(app.pickup_label(''), '')

    def test_unknown_code_returns_empty_string(self):
        self.assertEqual(app.pickup_label('taipei'), '')


class IsValidPickupTest(unittest.TestCase):
    def test_accepts_known_codes(self):
        self.assertTrue(app.is_valid_pickup('tamsui'))
        self.assertTrue(app.is_valid_pickup('qiyan'))

    def test_rejects_unknown_code(self):
        self.assertFalse(app.is_valid_pickup('taipei'))

    def test_rejects_none_and_empty(self):
        self.assertFalse(app.is_valid_pickup(None))
        self.assertFalse(app.is_valid_pickup(''))


class PlaceOrderPickupValidationTest(unittest.TestCase):
    """下單端點對取貨地點的驗證。這段驗證排在碰 DB 之前，所以不需要真的資料庫。"""

    def setUp(self):
        app.app.config['TESTING'] = True
        self.client = app.app.test_client()

    def _post(self, **overrides):
        body = {
            'customer_name': '王小明',
            'customer_phone': '0912345678',
            'packs': [{'quantity': 1, 'preferences': []}],
            'pickup_location': 'tamsui',
        }
        body.update(overrides)
        return self.client.post('/api/orders', json=body)

    def test_missing_pickup_returns_400(self):
        r = self._post(pickup_location=None)
        self.assertEqual(r.status_code, 400)
        self.assertIn('取貨地點', r.get_json()['error'])

    def test_unknown_pickup_returns_400(self):
        r = self._post(pickup_location='taipei')
        self.assertEqual(r.status_code, 400)

    def test_valid_pickup_passes_validation(self):
        # 合法地點 → 通過這關，繼續往下走到「必須先用 LINE 登入」才被擋
        r = self._post()
        self.assertEqual(r.status_code, 401)


if __name__ == '__main__':
    unittest.main()
