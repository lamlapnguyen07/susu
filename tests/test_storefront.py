"""多農場市集：單件商品計價、農場欄位、前台頁面渲染。"""
import unittest

from tests._stub_db import app

CATALOG = [
    {'id': 1, 'name': '紅奶油萵苣', 'kind': 'standard', 'price': 0, 'unit': '包', 'farm_id': None, 'is_available': True},
    {'id': 11, 'name': '羽衣甘藍', 'kind': 'special', 'price': 150, 'unit': '份', 'farm_id': None, 'is_available': True},
    {'id': 12, 'name': '芝麻葉', 'kind': 'special', 'price': 150, 'unit': '份', 'farm_id': None, 'is_available': False},
    {'id': 21, 'name': '有機乾燥香菇', 'kind': 'farm', 'price': 380, 'unit': '包', 'farm_id': 1, 'is_available': True},
]
FARMS = [{'id': 1, 'name': '添丁養生黑木耳', 'region': '桃園大溪', 'summary': 's', 'story': '', 'crops': '',
          'certification': '', 'website': '', 'photos': [], 'is_active': True, 'sort_order': 0}]


class PriceItemsTest(unittest.TestCase):
    def setUp(self):
        self._orig = app.get_farms
        app.get_farms = lambda include_inactive=False: FARMS

    def tearDown(self):
        app.get_farms = self._orig

    def test_prices_come_from_catalog_not_client(self):
        items, err = app._price_order_items(
            [{'product_id': 21, 'quantity': 2, 'price': 1}], catalog=CATALOG)
        self.assertIsNone(err)
        self.assertEqual(items[0]['price'], 380)
        self.assertEqual(items[0]['line_total'], 760)
        self.assertEqual(items[0]['source'], '添丁養生黑木耳')
        self.assertEqual(items[0]['name'], '有機乾燥香菇')

    def test_same_product_twice_is_merged(self):
        items, err = app._price_order_items(
            [{'product_id': 11, 'quantity': 1}, {'product_id': 11, 'quantity': 2}], catalog=CATALOG)
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['quantity'], 3)
        self.assertEqual(items[0]['source'], '食在好蔬')

    def test_lettuce_cannot_be_bought_as_unit_item(self):
        _, err = app._price_order_items([{'product_id': 1, 'quantity': 1}], catalog=CATALOG)
        self.assertTrue(err)

    def test_unknown_product_rejected(self):
        _, err = app._price_order_items([{'product_id': 999, 'quantity': 1}], catalog=CATALOG)
        self.assertTrue(err)

    def test_unavailable_product_rejected(self):
        _, err = app._price_order_items([{'product_id': 12, 'quantity': 1}], catalog=CATALOG)
        self.assertIn('暫無供應', err)

    def test_quantity_cap(self):
        _, err = app._price_order_items(
            [{'product_id': 11, 'quantity': app.ITEM_MAX_QTY + 1}], catalog=CATALOG)
        self.assertTrue(err)

    def test_garbage_input_rejected(self):
        _, err = app._price_order_items([{'product_id': 'abc', 'quantity': 1}], catalog=CATALOG)
        self.assertTrue(err)
        _, err = app._price_order_items(['x'], catalog=CATALOG)
        self.assertTrue(err)

    def test_zero_quantity_dropped(self):
        items, err = app._price_order_items([{'product_id': 11, 'quantity': 0}], catalog=CATALOG)
        self.assertIsNone(err)
        self.assertEqual(items, [])


class FieldsTest(unittest.TestCase):
    def test_farm_product_keeps_price_and_farm(self):
        f = app._read_product_fields({'name': '香菇', 'kind': 'farm', 'price': 380, 'farm_id': '3', 'unit': '盒'})
        self.assertEqual((f['kind'], f['price'], f['farm_id'], f['unit']), ('farm', 380, 3, '盒'))

    def test_own_product_never_has_farm(self):
        f = app._read_product_fields({'name': 'x', 'kind': 'special', 'price': 150, 'farm_id': 3})
        self.assertIsNone(f['farm_id'])

    def test_standard_price_forced_zero(self):
        f = app._read_product_fields({'name': 'x', 'kind': 'standard', 'price': 999})
        self.assertEqual(f['price'], 0)

    def test_unknown_kind_and_stock_fall_back(self):
        f = app._read_product_fields({'name': 'x', 'kind': 'evil', 'stock_status': 'nope'})
        self.assertEqual((f['kind'], f['stock_status']), ('standard', 'ok'))

    def test_farm_website_must_be_http(self):
        self.assertEqual(app._read_farm_fields({'name': 'a', 'website': 'javascript:alert(1)'})['website'], '')
        self.assertEqual(app._read_farm_fields({'name': 'a', 'website': 'https://x.tw'})['website'], 'https://x.tw')

    def test_to_dict_defaults(self):
        d = app._product_to_dict({'id': 1, 'name': 'x'})
        self.assertEqual((d['kind'], d['unit'], d['farm_id'], d['stock_status']), ('standard', '包', None, 'ok'))

    def test_items_to_lines(self):
        lines = app._items_to_lines([{'source': '添丁', 'name': '香菇', 'quantity': 2, 'unit': '包'}])
        self.assertEqual(lines, ['[添丁] 香菇 ×2包'])


class PagesTest(unittest.TestCase):
    """前台每一頁都能在有 / 沒有資料時正常渲染。"""

    def setUp(self):
        self._p, self._f = app.get_products, app.get_farms
        prods = [app._product_to_dict(dict(r, description='d', photo1='/img/menu/kale-1.jpg'))
                 for r in [dict(c, stock_status='ok') for c in CATALOG]]
        app.get_products = lambda: prods
        app.get_farms = lambda include_inactive=False: FARMS
        self.client = app.app.test_client()

    def tearDown(self):
        app.get_products, app.get_farms = self._p, self._f

    def test_pages_render(self):
        for path in ['/', '/shop', '/p/1', '/p/11', '/p/21', '/pack', '/farms', '/farms/1', '/story', '/partner', '/help']:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertIn('食在好蔬', r.get_data(as_text=True))

    def test_farm_product_page_shows_farm(self):
        html = self.client.get('/p/21').get_data(as_text=True)
        self.assertIn('添丁養生黑木耳', html)
        self.assertIn('NT$380', html)

    def test_missing_pages_404(self):
        self.assertEqual(self.client.get('/p/9999').status_code, 404)
        self.assertEqual(self.client.get('/farms/9999').status_code, 404)

    def test_inactive_farm_hides_its_goods(self):
        app.get_farms = lambda include_inactive=False: []
        self.assertEqual(self.client.get('/p/21').status_code, 404)

    def test_pages_render_without_db(self):
        app.get_products = lambda: []
        app.get_farms = lambda include_inactive=False: []
        for path in ['/', '/shop', '/farms', '/story', '/partner', '/help', '/pack']:
            self.assertEqual(self.client.get(path).status_code, 200, path)


if __name__ == '__main__':
    unittest.main()
