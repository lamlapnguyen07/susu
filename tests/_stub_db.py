"""測試共用：在 import app 之前塞假的 mysql.connector，避免匯入時真的連線 DB。

用法：
    from tests._stub_db import app
"""
import importlib
import sys
import types

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
