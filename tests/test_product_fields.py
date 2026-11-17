import importlib, sys, types, unittest

# 載入 app.py 但避免匯入時連線 DB：先放一個假的 mysql.connector
sys.modules.setdefault('mysql', types.ModuleType('mysql'))
_conn = types.ModuleType('mysql.connector')
_conn.connect = lambda **k: (_ for _ in ()).throw(RuntimeError('no db in test'))
_conn.Error = type('Error', (Exception,), {})
_pooling = types.ModuleType('mysql.connector.pooling')
_pooling.MySQLConnectionPool = lambda **k: (_ for _ in ()).throw(RuntimeError('no db in test'))
_conn.pooling = _pooling
sys.modules['mysql.connector'] = _conn
sys.modules['mysql.connector.pooling'] = _pooling
sys.modules['mysql'].connector = _conn

app = importlib.import_module('app')


class ProductFieldsTest(unittest.TestCase):
    def test_read_includes_nutrition(self):
        f = app._read_product_fields({'name': 'x', 'nutrition': '維他命B3、鉀'})
        self.assertEqual(f['nutrition'], '維他命B3、鉀')

    def test_read_nutrition_defaults_empty(self):
        f = app._read_product_fields({'name': 'x'})
        self.assertEqual(f['nutrition'], '')

    def test_to_dict_includes_nutrition(self):
        d = app._product_to_dict({'id': 1, 'name': 'x', 'nutrition': '鉀'})
        self.assertEqual(d['nutrition'], '鉀')

    def test_to_dict_nutrition_defaults_empty(self):
        d = app._product_to_dict({'id': 1, 'name': 'x'})
        self.assertEqual(d['nutrition'], '')


if __name__ == '__main__':
    unittest.main()
