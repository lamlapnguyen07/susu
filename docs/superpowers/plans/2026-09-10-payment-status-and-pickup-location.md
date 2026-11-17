# 付款狀態拆分 + 取貨地點 + LINE 通知差異化 實現計劃

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推薦）或 superpowers:executing-plans 逐任務實現此計劃。步驟使用複選框（`- [ ]`）語法來跟踪進度。

**目標：** 把 `Order.payment_method` 拆成「付款方式／收款狀態／接單與否」三個獨立語意，並為訂單加上取貨地點（淡水／奇岩），讓 LINE 通知依地點顯示正確樓層。

**架構：** 地點資料只存在 `app.py` 的 `PICKUP_LOCATIONS` 一份常數表；`app.py` 把組好的完整描述字串放進 LINE 佇列 payload，`push_sever.py` 只做字串代入、不持有任何地點知識。付款三語意各自獨立成欄，既有資料以一次性、帶旗標保護的遷移轉換。

**技術棧：** Flask + mysql-connector-python + python-docx；前端原生 JS（無框架）；測試用 `unittest`（`tests/` 目錄，以假的 `mysql.connector` 模組避免匯入時連線 DB）。

**規格：** [`docs/superpowers/specs/2026-09-10-payment-status-and-pickup-location-design.md`](../specs/2026-09-10-payment-status-and-pickup-location-design.md)

---

## 文件結構

| 檔案 | 職責 | 動作 |
|---|---|---|
| `tests/_stub_db.py` | 測試共用：在 `import app` 前塞假的 `mysql.connector`，並匯出 `app` | **建立** |
| `tests/test_pickup_location.py` | 地點設定表與驗證函式的測試 | **建立** |
| `tests/test_payment_migration.py` | 遷移 SQL 順序與旗標保護的測試 | **建立** |
| `tests/test_notification_payload.py` | LINE payload 組裝與付款措辭的測試 | **建立** |
| `app.py` | 地點常數表、DB schema／遷移、所有訂單 API、出貨單 | 修改 |
| `push_sever.py` | LINE 訊息模板（只做字串代入） | 修改 |
| `templates/pos.html` | 顧客端取貨地點 radio、訂單成立 modal | 修改 |
| `static/js/pos.js` | 取貨地點取值／驗證／送單／modal 填值 | 修改 |
| `static/css/pos.css` | 取貨地點 radio 樣式 | 修改 |
| `templates/admin.html` | 訂單表頭加欄、側邊欄加地點篩選 | 修改 |
| `static/js/admin.js` | 付款雙下拉、地點下拉、按鈕條件、篩選參數 | 修改 |
| `static/js/redeem.js` | 核銷頁付款 chip 判斷、顯示取貨地點 | 修改 |
| `static/css/admin.css` | 付款雙下拉與地點下拉樣式 | 修改 |
| `NAS_Deploy/app/*`、`NAS_Deploy/push_server/*` | 部署副本 | 最後一次性同步 |

**分解原則：** 任務 1–2 是純函式（可完整 TDD）；任務 3–9 是伺服器端，逐個端點修改；任務 10–11 是前端，以手動驗收確認；任務 12 收尾同步。每個任務結束都是一個可獨立 commit 的完整變更。

---

## 任務 0：前置檢查

**文件：** 無（唯讀檢查）

- [ ] **步驟 1：確認測試指令可跑**

執行：

```bash
python -m unittest tests.test_product_fields -v
```

預期：`Ran 4 tests ... OK`（結尾會印一行 `[db] 連線池建立失敗，fallback` 是正常的，代表 DB stub 生效）

> ⚠️ **不要用 `python -m unittest discover`** —— `tests/` 沒有 `__init__.py`，discover 會直接報 `ImportError: Start directory is not importable`。本計畫一律用明確模組清單（`python -m unittest tests.test_a tests.test_b`），那個形式靠命名空間套件運作，是可行的。

- [ ] **步驟 2：確認本 repo 目前沒有任何 commit**

執行：

```bash
git log --oneline -5
```

預期：無輸出（這個 repo 尚未有第一個 commit）。因此**每個任務的 commit 一律只 `git add` 該任務列出的檔案，絕對不要用 `git add -A` 或 `git add .`**。

- [ ] **步驟 3：⚠️ 確認 `.gitignore` 的失效樣式**

執行：

```bash
git check-ignore -v 2fa_setup_qr.png
```

預期：**exit code 1、無輸出** —— 代表這個檔案**沒有**被忽略。

原因：`.gitignore` 第 10 行寫成 `2fa_setup_qr.png      # 2FA 設定 QR（內含 TOTP 密鑰，絕不入版控）`，但 `.gitignore` **不支援同一行的 `#` 註解**，整行被當成一個字面樣式，因此完全沒有生效。`2fa_setup_qr.png` 內含後台 2FA 的 TOTP 密鑰。

**本計畫不修改 `.gitignore`（超出需求範圍）。** 防護方式是步驟 2 的規則：只 `git add` 明列的檔案。若要順手修掉，把第 10 行的註解移到上一行獨立成行即可 —— 但那是另一個決定，請先向使用者確認。

---

## 任務 1：取貨地點設定表與驗證函式

**文件：**
- 建立：`tests/_stub_db.py`
- 建立：`tests/test_pickup_location.py`
- 修改：`app.py`（在第 96 行 `DAILY_PACK_LIMIT` 之後、第 114 行 `# ==================== Admin 操作 audit log ====================` 之前插入新區塊）

- [ ] **步驟 1：建立測試共用的 DB stub**

建立 `tests/_stub_db.py`：

```python
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
```

- [ ] **步驟 2：編寫失敗的測試**

建立 `tests/test_pickup_location.py`：

```python
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


if __name__ == '__main__':
    unittest.main()
```

- [ ] **步驟 3：執行測試驗證失敗**

執行：

```bash
python -m unittest tests.test_pickup_location -v
```

預期：FAIL，報 `AttributeError: module 'app' has no attribute 'pickup_label'`

- [ ] **步驟 4：編寫最少實現代碼**

在 `app.py` 第 96 行（`DAILY_PACK_LIMIT = ...`）之後插入：

```python


# ==================== 取貨地點 ====================
# 全系統唯一的地點資料源。加點或樓層搬動只改這一份；
# push_sever.py 不持有任何地點知識，它只把 payload 裡組好的字串代入訊息。
PICKUP_LOCATIONS = {
    'tamsui': {'name': '淡水廠', 'spot': '2 樓大廳', 'label': '淡水廠　2 樓大廳'},
    'qiyan':  {'name': '奇岩廠', 'spot': '1 樓大廳', 'label': '奇岩廠　1 樓大廳'},
}


def pickup_label(loc):
    """地點代碼 → 顧客看到的完整描述。未知或 None（舊訂單）回空字串。"""
    return PICKUP_LOCATIONS.get(loc or '', {}).get('label', '')


def pickup_name(loc):
    """地點代碼 → 短名稱（出貨單表格用）。未知或 None 回 '—'。"""
    return PICKUP_LOCATIONS.get(loc or '', {}).get('name', '—')


def is_valid_pickup(loc):
    """是否為合法的地點代碼。"""
    return loc in PICKUP_LOCATIONS
```

- [ ] **步驟 5：執行測試驗證通過**

執行：

```bash
python -m unittest tests.test_pickup_location -v
```

預期：`Ran 8 tests ... OK`

- [ ] **步驟 6：Commit**

```bash
git add tests/_stub_db.py tests/test_pickup_location.py app.py
git commit -m "$(cat <<'EOF'
feat: 新增取貨地點設定表與驗證函式

淡水廠 2 樓大廳、奇岩廠 1 樓大廳。地點資料集中在 PICKUP_LOCATIONS
一份常數表，之後加點或樓層搬動只需改這裡。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 任務 2：付款欄位拆分的遷移邏輯

**文件：**
- 建立：`tests/test_payment_migration.py`
- 修改：`app.py`（在 `init_db` 函式之前，約第 295 行 `def init_db():` 上方插入）

- [ ] **步驟 1：編寫失敗的測試**

建立 `tests/test_payment_migration.py`：

```python
import unittest

from tests._stub_db import app


class FakeCursor:
    """記錄所有 execute 的 SQL；fetchone 固定回傳旗標值。"""

    def __init__(self, flag_value=0):
        self.executed = []
        self._flag = flag_value

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchone(self):
        return (self._flag,)

    def order_updates(self):
        return [s for s in self.executed if s.startswith('UPDATE `Order`')]


class MigrationFlagTest(unittest.TestCase):
    def test_runs_when_flag_is_zero(self):
        cur = FakeCursor(0)
        self.assertTrue(app._migrate_payment_split(cur))
        self.assertEqual(len(cur.order_updates()), 5)

    def test_skips_when_flag_is_one(self):
        cur = FakeCursor(1)
        self.assertFalse(app._migrate_payment_split(cur))
        self.assertEqual(cur.order_updates(), [])

    def test_sets_flag_after_running(self):
        cur = FakeCursor(0)
        app._migrate_payment_split(cur)
        self.assertTrue(any('UPDATE site_stats SET v = 1' in s for s in cur.executed))


class MigrationOrderTest(unittest.TestCase):
    """執行順序是正確性關鍵：unpaid→cash 必須最後跑，
    否則前面 WHERE payment_method='cash' 的兩條會再抓到它們，把 is_confirmed 誤設為 1。"""

    def setUp(self):
        self.cur = FakeCursor(0)
        app._migrate_payment_split(self.cur)
        self.updates = self.cur.order_updates()

    def test_unpaid_step_is_last(self):
        self.assertIn("WHERE payment_method='unpaid'", self.updates[-1])

    def test_all_cash_steps_run_before_unpaid_step(self):
        unpaid_i = next(i for i, s in enumerate(self.updates)
                        if "WHERE payment_method='unpaid'" in s)
        cash_i = [i for i, s in enumerate(self.updates)
                  if "WHERE payment_method='cash'" in s]
        self.assertEqual(len(cash_i), 2)
        self.assertTrue(all(i < unpaid_i for i in cash_i))


class MigrationRulesTest(unittest.TestCase):
    def setUp(self):
        self.cur = FakeCursor(0)
        app._migrate_payment_split(self.cur)
        self.updates = self.cur.order_updates()

    def _stmt_for(self, where_fragment):
        return next(s for s in self.updates if where_fragment in s)

    def test_picked_up_cash_becomes_paid(self):
        s = self._stmt_for("WHERE payment_method='cash' AND is_picked_up=1")
        self.assertIn("payment_status='paid'", s)
        self.assertIn("is_confirmed=1", s)

    def test_unpicked_cash_becomes_unpaid(self):
        s = self._stmt_for("WHERE payment_method='cash' AND is_picked_up=0")
        self.assertIn("payment_status='unpaid'", s)
        self.assertIn("is_confirmed=1", s)

    def test_transfer_becomes_paid(self):
        s = self._stmt_for("WHERE payment_method='transfer'")
        self.assertIn("payment_status='paid'", s)

    def test_pending_check_becomes_transfer_pending(self):
        s = self._stmt_for("WHERE payment_method='pending_check'")
        self.assertIn("payment_method='transfer'", s)
        self.assertIn("payment_status='pending_check'", s)

    def test_unpaid_becomes_unconfirmed_cash(self):
        s = self._stmt_for("WHERE payment_method='unpaid'")
        self.assertIn("is_confirmed=0", s)
        self.assertIn("payment_method='cash'", s)
        self.assertIn("payment_status='unpaid'", s)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **步驟 2：執行測試驗證失敗**

執行：

```bash
python -m unittest tests.test_payment_migration -v
```

預期：FAIL，報 `AttributeError: module 'app' has no attribute '_migrate_payment_split'`

- [ ] **步驟 3：編寫最少實現代碼**

在 `app.py` 的 `def init_db():`（約第 295 行）**上方**插入：

```python
# ==================== 付款欄位拆分遷移 ====================
# 舊版 payment_method 一欄同時表示「接單 / 方式 / 收款」三種語意，
# 拆成 payment_method（方式）+ payment_status（收款）+ is_confirmed（接單）。
_PAYMENT_SPLIT_FLAG = 'migration_payment_split'

# 順序有意義，不可調換：unpaid→cash 那條必須最後跑。
# 若它先跑，前兩條 WHERE payment_method='cash' 會再抓到這批剛遷好的資料，
# 把還沒接單的訂單誤設成 is_confirmed=1。
_PAYMENT_SPLIT_STEPS = [
    "UPDATE `Order` SET is_confirmed=1, payment_method='cash', payment_status='paid' "
    "WHERE payment_method='cash' AND is_picked_up=1",

    "UPDATE `Order` SET is_confirmed=1, payment_method='cash', payment_status='unpaid' "
    "WHERE payment_method='cash' AND is_picked_up=0",

    "UPDATE `Order` SET is_confirmed=1, payment_method='transfer', payment_status='paid' "
    "WHERE payment_method='transfer'",

    "UPDATE `Order` SET is_confirmed=1, payment_method='transfer', payment_status='pending_check' "
    "WHERE payment_method='pending_check'",

    "UPDATE `Order` SET is_confirmed=0, payment_method='cash', payment_status='unpaid' "
    "WHERE payment_method='unpaid'",
]


def _migrate_payment_split(cur):
    """一次性把舊的單欄 payment_method 拆成三欄。以 site_stats 旗標保護，重啟不重跑。

    回傳 True = 這次有執行；False = 之前已跑過，略過。
    """
    cur.execute("INSERT IGNORE INTO site_stats (k, v) VALUES (%s, 0)", (_PAYMENT_SPLIT_FLAG,))
    cur.execute("SELECT v FROM site_stats WHERE k = %s", (_PAYMENT_SPLIT_FLAG,))
    row = cur.fetchone()
    if row and int(row[0]) == 1:
        return False
    for stmt in _PAYMENT_SPLIT_STEPS:
        cur.execute(stmt)
    cur.execute("UPDATE site_stats SET v = 1 WHERE k = %s", (_PAYMENT_SPLIT_FLAG,))
    return True
```

- [ ] **步驟 4：執行測試驗證通過**

執行：

```bash
python -m unittest tests.test_payment_migration -v
```

預期：`Ran 10 tests ... OK`

- [ ] **步驟 5：Commit**

```bash
git add tests/test_payment_migration.py app.py
git commit -m "$(cat <<'EOF'
feat: 新增付款欄位拆分的一次性遷移邏輯

以 site_stats 旗標保護，重啟不重跑。UPDATE 順序有正確性意義：
unpaid→cash 必須最後執行，否則會被前面的 cash 條件重複抓到。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 任務 3：資料表新欄位並接上遷移

**文件：**
- 修改：`app.py` `init_db()`（約第 296–400 行）

- [ ] **步驟 1：新增欄位的 ALTER 語句**

在 `init_db()` 內、`Order` 表那組 `for stmt in [...]` 清單（約第 311–328 行）的**最後一項之後**，加入四行：

```python
            # 核銷時間戳（店員掃碼確認取貨時寫入）
            "ALTER TABLE `Order` ADD COLUMN picked_up_at TIMESTAMP NULL DEFAULT NULL",
            # === 付款三語意拆分 ===
            "ALTER TABLE `Order` ADD COLUMN payment_status VARCHAR(20) DEFAULT 'unpaid'",
            "ALTER TABLE `Order` ADD COLUMN is_confirmed BOOLEAN DEFAULT FALSE",
            # payment_method 語意收窄成「只管方式」，預設值不能再是 'unpaid'
            "ALTER TABLE `Order` MODIFY COLUMN payment_method VARCHAR(20) DEFAULT 'cash'",
            # === 取貨地點 ===
            "ALTER TABLE `Order` ADD COLUMN pickup_location VARCHAR(20) DEFAULT NULL",
```

（前兩行是既有內容，貼在這裡只為標定插入位置。）

- [ ] **步驟 2：在 commit 之前呼叫遷移**

`init_db()` 尾端目前是：

```python
        cur.execute("""
            CREATE TABLE IF NOT EXISTS site_visitors (
                ip VARCHAR(45) PRIMARY KEY,
                first_visit DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
```

改成（遷移必須排在 `site_stats` 建表之後，因為它要寫旗標）：

```python
        cur.execute("""
            CREATE TABLE IF NOT EXISTS site_visitors (
                ip VARCHAR(45) PRIMARY KEY,
                first_visit DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 付款三語意拆分（只跑一次）。
        # 失敗時外層 except 會接住，此處的 conn.commit() 不會執行 →
        # 未提交的 UPDATE 在連線關閉時自動回滾，旗標也不會寫入，下次啟動重試。
        if _migrate_payment_split(cur):
            print("[init_db] 已執行付款欄位拆分遷移")
        conn.commit()
```

- [ ] **步驟 3：驗證語法無誤**

執行：

```bash
python -c "import ast,sys; ast.parse(open('app.py',encoding='utf-8').read()); print('app.py 語法 OK')"
```

預期：`app.py 語法 OK`

- [ ] **步驟 4：既有測試仍通過**

執行：

```bash
python -m unittest tests.test_product_fields tests.test_pickup_location tests.test_payment_migration -v
```

預期：`Ran 22 tests ... OK`

- [ ] **步驟 5：Commit**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
feat: Order 表新增 payment_status / is_confirmed / pickup_location 並接上遷移

payment_method 預設值改為 cash（語意收窄成只管付款方式）。
遷移排在 site_stats 建表之後，因為需要寫入旗標。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 任務 4：下單時驗證並寫入取貨地點

**文件：**
- 修改：`tests/test_pickup_location.py`
- 修改：`app.py` `api_place_order()`（約第 1101–1263 行）

- [ ] **步驟 1：編寫失敗的端點測試**

在 `tests/test_pickup_location.py` 的 `IsValidPickupTest` 類別之後、`if __name__` 之前插入：

```python
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
```

- [ ] **步驟 2：執行測試驗證失敗**

執行：

```bash
python -m unittest tests.test_pickup_location -v
```

預期：三個新測試都 FAIL —— 缺地點與非法地點會回 401（因為驗證還不存在，直接走到登入檢查），而非預期的 400。

- [ ] **步驟 3：加入地點驗證**

在 `api_place_order` 的電話格式驗證之後、`packs = data.get('packs') or []` 之前（約第 1123 行），插入：

```python
    # 取貨地點：必填，且必須是已知代碼（防前端竄改）
    pickup_location = (data.get('pickup_location') or '').strip()
    if not is_valid_pickup(pickup_location):
        return jsonify({"ok": False, "error": "請選擇取貨地點"}), 400
```

- [ ] **步驟 4：改寫 INSERT**

把 `api_place_order` 內的 INSERT（約第 1233–1241 行）：

```python
        cur.execute("""
            INSERT INTO `Order`
            (order_id, customer_name, customer_phone, grand_total, order_details,
             payment_method, is_notified, is_picked_up, line_id,
             expected_ship_date, total_quantity)
            VALUES (%s, %s, %s, %s, %s, 'unpaid', FALSE, FALSE, %s, %s, %s)
        """, (order_id, name, phone, grand_total,
              json.dumps(payload, ensure_ascii=False),
              line_id, expected_ship_date, total_quantity))
```

改成：

```python
        cur.execute("""
            INSERT INTO `Order`
            (order_id, customer_name, customer_phone, grand_total, order_details,
             payment_method, payment_status, is_confirmed, is_notified, is_picked_up,
             line_id, expected_ship_date, total_quantity, pickup_location)
            VALUES (%s, %s, %s, %s, %s, 'cash', 'unpaid', FALSE, FALSE, FALSE, %s, %s, %s, %s)
        """, (order_id, name, phone, grand_total,
              json.dumps(payload, ensure_ascii=False),
              line_id, expected_ship_date, total_quantity, pickup_location))
```

- [ ] **步驟 5：執行測試驗證通過**

執行：

```bash
python -m unittest tests.test_pickup_location -v
```

預期：`Ran 11 tests ... OK`

- [ ] **步驟 6：Commit**

```bash
git add tests/test_pickup_location.py app.py
git commit -m "$(cat <<'EOF'
feat: 下單時必填取貨地點並寫入訂單

新訂單的付款欄位改為 cash/unpaid/is_confirmed=FALSE，
接單狀態不再靠 payment_method='unpaid' 表示。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 任務 5：後台修改取貨地點端點

**文件：**
- 修改：`app.py`（新端點加在 `api_update_payment` 之後，約第 2082 行）

- [ ] **步驟 1：新增端點**

在 `app.py` 的 `api_update_payment` 函式結束之後、`@app.route('/api/orders/notify', ...)` 之前插入：

```python
@app.route('/api/orders/<order_id>/pickup-location', methods=['POST'])
@login_required
@csrf_required
def api_update_pickup_location(order_id):
    """後台修改取貨地點。僅限 active 且未取貨的訂單。"""
    loc = ((request.get_json() or {}).get('pickup_location') or '').strip()
    if not is_valid_pickup(loc):
        return jsonify({"ok": False, "error": "invalid pickup_location"}), 400
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        # 先查狀態再更新：不能用 UPDATE 的 rowcount 判斷，
        # 因為選回「原本就相同的地點」時 MySQL 也會回 0，會被誤判成 409。
        cur.execute(
            "SELECT order_status, is_picked_up, pickup_location FROM `Order` WHERE order_id = %s",
            (order_id,)
        )
        order = cur.fetchone()
        if not order:
            cur.close(); conn.close()
            return jsonify({"ok": False, "error": "訂單不存在"}), 404
        if order['order_status'] != 'active' or order['is_picked_up']:
            cur.close(); conn.close()
            return jsonify({"ok": False,
                            "error": "此訂單已取貨或已取消，無法修改取貨地點"}), 409

        cur.execute("UPDATE `Order` SET pickup_location=%s WHERE order_id=%s",
                    (loc, order_id))
        conn.commit()
        cur.close()
        conn.close()
        audit('update_pickup_location', order_id=order_id,
              from_=order.get('pickup_location') or 'none', to_=loc)
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[update_pickup_location] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500


```

- [ ] **步驟 2：驗證語法無誤**

執行：

```bash
python -c "import ast,sys; ast.parse(open('app.py',encoding='utf-8').read()); print('app.py 語法 OK')"
```

預期：`app.py 語法 OK`

- [ ] **步驟 3：Commit**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
feat: 新增後台修改取貨地點端點

先 SELECT 查狀態再 UPDATE，避免「選回原本相同地點」時
MySQL rowcount 為 0 被誤判成 409。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 任務 6：付款欄位語意拆分（伺服器端）

**文件：**
- 修改：`app.py` — `_PAY_LABELS`（第 1398 行）、`api_confirm_order`（第 1713 行）、`api_update_payment`（第 2044 行）、`api_pay_summary`（第 1365 行）、`api_list_orders`（第 1265 行）、`api_customer_cancel`（第 1619 行）、`api_redeem_lookup`（第 1792 行）、`api_orders_mine`（第 1968 行）

> ⚠️ **本任務有三個容易漏掉的連鎖依賴**（步驟 6–8）：顧客自助取消、店員核銷頁、顧客查訂單這三處都用 `payment_method='unpaid'` 當「還沒接單」的判斷。拆分後 `payment_method` 不再有 `unpaid` 這個值，漏改會讓這些功能**靜默失效**（例如顧客再也無法自己取消訂單，且不會有任何錯誤訊息）。

- [ ] **步驟 1：替換標籤常數**

把 `app.py` 約第 1397–1400 行的：

```python
_PAY_LABELS = {
    'unpaid': '未確認', 'pending_check': '待對帳',
    'cash': '取貨付現', 'transfer': '轉帳',
}
```

改成：

```python
# 後台／出貨單顯示用（方式與狀態各一組，不再混用）
_PAY_METHOD_LABELS = {'cash': '現金', 'transfer': '轉帳'}
_PAY_STATUS_LABELS = {'unpaid': '未收款', 'pending_check': '待對帳', 'paid': '已收款'}

# 端點驗證用的值域
_PAY_METHODS = ('cash', 'transfer')
_PAY_STATUSES = ('unpaid', 'pending_check', 'paid')

# LINE 訊息用的措辭（與後台標籤分開，語氣不同）
_PAY_METHOD_TEXT = {'cash': '取貨付現', 'transfer': '轉帳'}
# 尾句必須跟著方式走，否則轉帳單會出現「前來取貨並付現金」的矛盾指示
_PAY_REMINDER = {'cash': '並付現金', 'transfer': ''}
```

- [ ] **步驟 2：改寫 `api_confirm_order` 的狀態判斷與更新**

把 `api_confirm_order`（約第 1722–1740 行）中的 SELECT、狀態檢查、UPDATE 三段：

```python
        cur.execute("""
            SELECT customer_phone, grand_total, payment_method, order_status, expected_ship_date
            FROM `Order` WHERE order_id = %s
        """, (order_id,))
        order = cur.fetchone()
        if not order:
            cur.close(); conn.close()
            return jsonify({"ok": False, "error": "訂單不存在"}), 404
        if order['order_status'] != 'active' or order['payment_method'] != 'unpaid':
            cur.close(); conn.close()
            return jsonify({"ok": False, "error": "此訂單無法確認（已確認或已是終態）"}), 409

        qr_token = secrets.token_urlsafe(8)
        cur.execute(
            "UPDATE `Order` SET payment_method='cash', qr_token=%s WHERE order_id=%s",
            (qr_token, order_id)
        )
```

改成：

```python
        cur.execute("""
            SELECT customer_phone, grand_total, payment_method, is_confirmed,
                   order_status, expected_ship_date, pickup_location
            FROM `Order` WHERE order_id = %s
        """, (order_id,))
        order = cur.fetchone()
        if not order:
            cur.close(); conn.close()
            return jsonify({"ok": False, "error": "訂單不存在"}), 404
        if order['order_status'] != 'active' or order['is_confirmed']:
            cur.close(); conn.close()
            return jsonify({"ok": False, "error": "此訂單無法確認（已確認或已是終態）"}), 409

        qr_token = secrets.token_urlsafe(8)
        # 只標記「已接單」，不再碰 payment_method（那是付款方式，不是接單狀態）
        cur.execute(
            "UPDATE `Order` SET is_confirmed=TRUE, qr_token=%s WHERE order_id=%s",
            (qr_token, order_id)
        )
```

（此函式的 payload 在任務 7 處理。）

- [ ] **步驟 3：改寫 `api_update_payment`**

把整個 `api_update_payment` 函式（約第 2044–2082 行）替換為：

```python
@app.route('/api/orders/<order_id>/payment', methods=['POST'])
@login_required
@csrf_required
def api_update_payment(order_id):
    """後台更新付款方式／收款狀態。method 與 status 皆為選填，只更新有帶的那個。
    純標記，不推 LINE、不碰 QR。"""
    data = request.get_json() or {}
    method = data.get('method')
    status = data.get('status')
    if method is None and status is None:
        return jsonify({"ok": False, "error": "未指定要更新的欄位"}), 400
    if method is not None and method not in _PAY_METHODS:
        return jsonify({"ok": False, "error": "invalid method"}), 400
    if status is not None and status not in _PAY_STATUSES:
        return jsonify({"ok": False, "error": "invalid status"}), 400
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT payment_method, payment_status FROM `Order` WHERE order_id = %s",
            (order_id,)
        )
        order = cur.fetchone()
        if not order:
            cur.close()
            conn.close()
            return jsonify({"ok": False, "error": "訂單不存在"}), 404

        # 只組有帶到的欄位；欄位名是程式內定值，值一律走參數綁定
        sets, params = [], []
        if method is not None:
            sets.append("payment_method=%s")
            params.append(method)
        if status is not None:
            sets.append("payment_status=%s")
            params.append(status)
        params.append(order_id)
        cur.execute(f"UPDATE `Order` SET {', '.join(sets)} WHERE order_id=%s", tuple(params))

        conn.commit()
        cur.close()
        conn.close()
        audit('update_payment', order_id=order_id,
              from_=f"{order['payment_method']}/{order['payment_status']}",
              to_=f"{method or order['payment_method']}/{status or order['payment_status']}")
        return jsonify({"ok": True, "queued": False})
    except Exception as e:
        print(f"[update_payment] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500
```

- [ ] **步驟 4：改寫 `api_pay_summary` 的統計**

把 `api_pay_summary`（約第 1372–1380 行）的 SQL：

```python
            SELECT
                SUM(CASE WHEN payment_method='unpaid' THEN 1 ELSE 0 END) AS unpaid,
                SUM(CASE WHEN payment_method='pending_check' THEN 1 ELSE 0 END) AS pending_check,
                SUM(CASE WHEN payment_method IN ('cash','transfer') THEN 1 ELSE 0 END) AS paid
            FROM `Order`
            WHERE is_picked_up = FALSE
```

改成：

```python
            SELECT
                SUM(CASE WHEN payment_status='unpaid' THEN 1 ELSE 0 END) AS unpaid,
                SUM(CASE WHEN payment_status='pending_check' THEN 1 ELSE 0 END) AS pending_check,
                SUM(CASE WHEN payment_status='paid' THEN 1 ELSE 0 END) AS paid
            FROM `Order`
            WHERE is_picked_up = FALSE
```

- [ ] **步驟 5：改寫 `api_list_orders` 的篩選**

在 `api_list_orders` 讀取查詢參數處（約第 1271 行 `pay_status = ...` 之後）加一行：

```python
    pickup = (request.args.get('pickup') or '').strip()          # '' | 'tamsui' | 'qiyan'
```

把付款篩選那五行（約第 1296–1304 行）：

```python
            if pay_status == 'pending':          # 尚未收款（未付款 + 待對帳）
                cond.append("payment_method IN ('unpaid','pending_check')")
            elif pay_status == 'unpaid':
                cond.append("payment_method = 'unpaid'")
            elif pay_status == 'pending_check':
                cond.append("payment_method = 'pending_check'")
            elif pay_status == 'paid':           # 已收款（現金 + 轉帳）
                cond.append("payment_method IN ('cash','transfer')")
```

改成：

```python
            if pay_status == 'pending':          # 尚未收款（未收款 + 待對帳）
                cond.append("payment_status IN ('unpaid','pending_check')")
            elif pay_status == 'unpaid':
                cond.append("payment_status = 'unpaid'")
            elif pay_status == 'pending_check':
                cond.append("payment_status = 'pending_check'")
            elif pay_status == 'paid':
                cond.append("payment_status = 'paid'")
            # 取貨地點篩選
            if pickup and is_valid_pickup(pickup):
                cond.append("pickup_location = %s")
                params.append(pickup)
```

- [ ] **步驟 6：改寫 `api_customer_cancel`（顧客自助取消）**

顧客只能在店家「還沒接單」前自行取消。把 `api_customer_cancel`（約第 1621–1642 行）的 docstring 與 UPDATE 條件：

```python
    """顧客自行取消「未付款」訂單。
    安全：四重條件一次擋掉越權 + 越狀態 —
      line_id=自己 AND order_status='active' AND payment_method='unpaid'。"""
```

改成：

```python
    """顧客自行取消「店家尚未接單」的訂單。
    安全：四重條件一次擋掉越權 + 越狀態 —
      line_id=自己 AND order_status='active' AND is_confirmed=FALSE。"""
```

並把同一函式內 UPDATE 的最後一個條件：

```python
              AND payment_method='unpaid'
```

改成：

```python
              AND is_confirmed=FALSE
```

- [ ] **步驟 7：改寫 `api_redeem_lookup`（店員核銷頁）**

把 `api_redeem_lookup` 的 SELECT（約第 1804–1808 行）：

```python
            SELECT order_id, customer_name, customer_phone, grand_total,
                   order_details, payment_method, order_status,
                   is_picked_up, picked_up_at, qr_token, expected_ship_date
            FROM `Order` WHERE order_id = %s
```

改成：

```python
            SELECT order_id, customer_name, customer_phone, grand_total,
                   order_details, payment_method, payment_status, is_confirmed,
                   order_status, is_picked_up, picked_up_at, qr_token,
                   expected_ship_date, pickup_location
            FROM `Order` WHERE order_id = %s
```

並在回傳的 `order` 物件中（約第 1834 行 `"payment_method": o['payment_method'],` 那行）改成三行：

```python
                "payment_method": o['payment_method'],
                "payment_status": o['payment_status'],
                "is_confirmed": bool(o['is_confirmed']),
                "pickup_label": pickup_label(o.get('pickup_location')),
```

- [ ] **步驟 8：改寫 `api_orders_mine`（顧客查自己的訂單）**

把 `api_orders_mine` 的 docstring（約第 1971 行）：

```python
    前端再依 payment_method / is_picked_up 分類成 待付款 / 待出貨 / 已完成。
```

改成：

```python
    前端再依 is_confirmed / payment_status / is_picked_up 分類。
```

並把 SELECT（約第 1980–1984 行）：

```python
            SELECT order_id, customer_name, customer_phone, grand_total,
                   payment_method, is_picked_up, is_notified,
                   order_status, cancelled_at, refunded_at,
                   expected_ship_date, order_time
```

改成：

```python
            SELECT order_id, customer_name, customer_phone, grand_total,
                   payment_method, payment_status, is_confirmed,
                   is_picked_up, is_notified,
                   order_status, cancelled_at, refunded_at,
                   expected_ship_date, order_time, pickup_location
```

並在其後的序列化迴圈中（約第 1998–2000 行 `r['is_notified'] = bool(...)` 那行之後）補一行：

```python
            r['is_confirmed'] = bool(r.get('is_confirmed'))
```

- [ ] **步驟 9：確認舊語意已無殘留**

執行：

```bash
grep -n "_PAY_LABELS\|payment_method='unpaid'\|payment_method = 'unpaid'\|payment_method='pending_check'" app.py
```

預期：只剩兩處，其餘皆無 ——
1. `_build_shipping_docx` 內的 `_PAY_LABELS`（第 1519 行附近）→ 任務 9 處理。
2. `_PAYMENT_SPLIT_STEPS` 內的遷移 SQL 字串（任務 2 新增）→ **這是遷移用的，本來就該保留**。

若出現其他位置，一併改用新欄位。

- [ ] **步驟 10：驗證語法無誤且既有測試通過**

執行：

```bash
python -c "import ast,sys; ast.parse(open('app.py',encoding='utf-8').read()); print('app.py 語法 OK')" && python -m unittest tests.test_product_fields tests.test_pickup_location tests.test_payment_migration -v
```

預期：`app.py 語法 OK`，接著 `Ran 25 tests ... OK`

- [ ] **步驟 11：Commit**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
refactor: 付款方式與收款狀態拆分（伺服器端）

confirm_order 改為只標記 is_confirmed，不再把 payment_method 硬寫成 cash；
pay_summary 與訂單篩選改讀 payment_status，修正「已接單即算已收款」的錯誤統計；
update_payment 改為 method / status 各自選填。
顧客自助取消、核銷頁、顧客查訂單三處的「未接單」判斷一併改用 is_confirmed，
否則 payment_method 不再有 unpaid 值會讓這些功能靜默失效。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 任務 7：LINE 通知 payload

**文件：**
- 建立：`tests/test_notification_payload.py`
- 修改：`app.py` — `api_confirm_order` 的 payload（約第 1744 行）、`api_notify`（約第 2098 行）

- [ ] **步驟 1：編寫失敗的測試**

建立 `tests/test_notification_payload.py`：

```python
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
```

- [ ] **步驟 2：執行測試驗證失敗**

執行：

```bash
python -m unittest tests.test_notification_payload -v
```

預期：FAIL，報 `AttributeError: module 'app' has no attribute '_build_arrived_payload'`

- [ ] **步驟 3：新增 payload 組裝函式**

在 `app.py` 的 `api_notify` 函式（約第 2084 行 `@app.route('/api/orders/notify', ...)`）**上方**插入：

```python
def _build_arrived_payload(order_id, loc):
    """到貨通知的 LINE payload。訂單編號一定要帶——
    同一支電話可能同時有兩張不同廠的訂單，沒有編號顧客無法分辨該去哪一廠。"""
    return {
        "order_id": str(order_id),
        "pickup_label": pickup_label(loc),
    }


```

- [ ] **步驟 4：`api_notify` 改為帶 payload**

把 `api_notify` 內的迴圈（約第 2096–2107 行）：

```python
        for oid in order_ids:
            cur.execute(
                "SELECT customer_phone, is_notified FROM `Order` WHERE order_id=%s",
                (oid,)
            )
            row = cur.fetchone()
            if row and not row['is_notified']:
                if enqueue_line_notification("order_arrived", row['customer_phone']):
```

改成：

```python
        for oid in order_ids:
            cur.execute(
                "SELECT customer_phone, is_notified, pickup_location FROM `Order` WHERE order_id=%s",
                (oid,)
            )
            row = cur.fetchone()
            if row and not row['is_notified']:
                if enqueue_line_notification(
                    "order_arrived", row['customer_phone'],
                    _build_arrived_payload(oid, row.get('pickup_location'))
                ):
```

- [ ] **步驟 5：`api_confirm_order` 的 payload 加三個欄位**

把 `api_confirm_order` 內的 `_enqueue_line_in_cursor` 呼叫（約第 1744–1753 行）：

```python
        queued = _enqueue_line_in_cursor(
            cur, 'order_confirmed', order['customer_phone'],
            {
                "order_id": order_id,
                "amount": int(order.get('grand_total') or 0),
                "qr_file": order_qr_filename(order_id, qr_token),
                "ship_date_text": ship_date_text,
            }
        )
```

改成：

```python
        _method = order.get('payment_method') or 'cash'
        queued = _enqueue_line_in_cursor(
            cur, 'order_confirmed', order['customer_phone'],
            {
                "order_id": order_id,
                "amount": int(order.get('grand_total') or 0),
                "qr_file": order_qr_filename(order_id, qr_token),
                "ship_date_text": ship_date_text,
                "pickup_label": pickup_label(order.get('pickup_location')),
                "payment_method_text": _PAY_METHOD_TEXT.get(_method, '取貨付現'),
                "pay_reminder": _PAY_REMINDER.get(_method, '並付現金'),
            }
        )
```

- [ ] **步驟 6：執行測試驗證通過**

執行：

```bash
python -m unittest tests.test_product_fields tests.test_pickup_location tests.test_payment_migration tests.test_notification_payload -v
```

預期：`Ran 33 tests ... OK`

- [ ] **步驟 7：Commit**

```bash
git add tests/test_notification_payload.py app.py
git commit -m "$(cat <<'EOF'
feat: LINE 通知 payload 帶上訂單編號與取貨地點

到貨通知原本只送電話、不帶任何 payload，worker 因此只能寫死「1F大廳」。
現在帶 order_id + 組好的地點描述，同一支電話有兩張不同廠訂單時也能分辨。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 任務 8：LINE 訊息模板

**文件：**
- 修改：`push_sever.py` `process_notification_queue()`（第 447–478 行）

- [ ] **步驟 1：改寫到貨通知**

把 `push_sever.py` 第 447–448 行：

```python
                            if row['type'] == 'order_arrived':
                                msg = f"🚚 【智慧農場 - 貨到通知】\n\n您好 {name}，\n您訂購的蔬菜已經到貨囉！\n請您盡速前往 1F大廳取件，享受最新鮮的蔬菜！🥬✨"
```

改成：

```python
                            if row['type'] == 'order_arrived':
                                # 地點文字由 app.py 組好放進 payload，這裡只做代入。
                                # 舊訂單沒有地點 → 整個 📍 段落省略，不會留下空白行。
                                arrived_order_id = payload.get('order_id', '')
                                arrived_pickup = payload.get('pickup_label', '')
                                msg = (
                                    f"🚚 【食在好蔬 - 到貨通知】\n\n"
                                    f"您好 {name}，\n"
                                    f"您訂購的蔬菜已經到貨囉！\n"
                                )
                                if arrived_order_id:
                                    msg += f"訂單編號：{arrived_order_id}\n"
                                if arrived_pickup:
                                    msg += f"\n📍 取貨地點：{arrived_pickup}\n"
                                msg += "\n請盡速前來取件，享受最新鮮的蔬菜！🥬✨"
```

- [ ] **步驟 2：改寫下單成功通知**

把第 449–474 行的 `elif row['type'] == 'order_confirmed':` 整段（從 `amount = payload.get(...)` 到 `f"確認庫存後會再通知您可取貨時間，感謝您的購買！"` 那組括號結束為止）替換為：

```python
                            elif row['type'] == 'order_confirmed':
                                # 現金（取貨付現）模式：店家「確認訂單」時通知顧客
                                amount = payload.get('amount', 0)
                                order_id = payload.get('order_id', '')
                                ship_date_text = payload.get('ship_date_text', '')
                                pickup_text = payload.get('pickup_label', '')
                                method_text = payload.get('payment_method_text', '取貨付現')
                                # 尾句跟著付款方式走：轉帳單為空字串，不會說成「付現金」
                                pay_reminder = payload.get('pay_reminder', '並付現金')
                                msg = (
                                    f"✅ 【食在好蔬 - 下單成功】\n\n"
                                    f"您好 {name}，\n"
                                    f"您的訂單已確認成立！\n"
                                    f"訂單編號：{order_id}\n"
                                    f"金額：NT$ {amount}（{method_text}）\n"
                                )
                                if pickup_text:
                                    msg += f"📍 取貨地點：{pickup_text}\n"
                                if ship_date_text:
                                    # 已指定取貨日 → 明確告知幾號取貨
                                    msg += (
                                        f"預計取貨日：{ship_date_text}\n\n"
                                        f"請於上述日期帶著下方的取貨 QR 條碼前來取貨{pay_reminder}。\n"
                                        f"感謝您的購買！"
                                    )
                                else:
                                    # 尚未指定取貨日 → 維持原本「確認後再通知」說法
                                    msg += (
                                        f"\n"
                                        f"請帶著下方的取貨 QR 條碼前來取貨{pay_reminder}。\n"
                                        f"確認庫存後會再通知您可取貨時間，感謝您的購買！"
                                    )
```

保留其後既有的 QR 圖網址組裝（`qr_file = payload.get('qr_file', '')` 那三行）不動。

- [ ] **步驟 3：驗證語法無誤**

執行：

```bash
python -c "import ast,sys; ast.parse(open('push_sever.py',encoding='utf-8').read()); print('push_sever.py 語法 OK')"
```

預期：`push_sever.py 語法 OK`

- [ ] **步驟 4：人工核對訊息排版**

執行以下腳本，肉眼確認四種情境的排版（有／無地點 × 有／無取貨日）都沒有多餘空白行：

```bash
python - <<'PY'
def arrived(name, order_id, pickup):
    msg = f"🚚 【食在好蔬 - 到貨通知】\n\n您好 {name}，\n您訂購的蔬菜已經到貨囉！\n"
    if order_id:
        msg += f"訂單編號：{order_id}\n"
    if pickup:
        msg += f"\n📍 取貨地點：{pickup}\n"
    msg += "\n請盡速前來取件，享受最新鮮的蔬菜！🥬✨"
    return msg

for label, args in [("淡水（新單）", ("王小明", "260910001", "淡水廠　2 樓大廳")),
                    ("奇岩（新單）", ("王小明", "260910002", "奇岩廠　1 樓大廳")),
                    ("舊單（無地點）", ("王小明", "260610003", ""))]:
    print(f"===== {label} =====")
    print(arrived(*args))
    print()
PY
```

預期：三段訊息中，「訂單編號」與「📍 取貨地點」之間恰好一個空行；舊單那段沒有殘留的 `📍` 或連續空行。

- [ ] **步驟 5：Commit**

```bash
git add push_sever.py
git commit -m "$(cat <<'EOF'
feat: LINE 訊息依取貨地點顯示正確樓層

到貨通知加上訂單編號與地點（淡水廠 2 樓大廳／奇岩廠 1 樓大廳），
不再寫死 1F大廳；標題由「智慧農場」統一為「食在好蔬」。
下單成功通知加地點，且尾句依付款方式決定是否提「付現金」。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 任務 9：出貨單加取貨地點欄

**文件：**
- 修改：`app.py` `_build_shipping_docx()`（第 1493、1519、1526–1535 行）、`api_export_shipping()`（第 1576–1582 行）

- [ ] **步驟 1：表頭加欄**

把 `_build_shipping_docx` 第 1493 行：

```python
    headers = ['訂單編號', '姓名', '電話', '品項', '份數', '金額', '付款', '備註', '狀態']
```

改成：

```python
    headers = ['訂單編號', '姓名', '電話', '取貨地點', '品項', '份數', '金額', '付款', '備註', '狀態']
```

- [ ] **步驟 2：改寫付款欄取值**

把第 1519 行：

```python
        pay = _PAY_LABELS.get(r.get('payment_method'), r.get('payment_method') or '')
```

改成：

```python
        # 付款欄印成兩行：方式／狀態
        pay = [
            _PAY_METHOD_LABELS.get(r.get('payment_method'), r.get('payment_method') or ''),
            _PAY_STATUS_LABELS.get(r.get('payment_status'), r.get('payment_status') or ''),
        ]
```

- [ ] **步驟 3：改寫欄位寫入（索引整體後移一位）**

把第 1527–1535 行：

```python
        _docx_set_cell(cells[0], str(r.get('order_id') or ''), CJK, shade=row_shade)
        _docx_set_cell(cells[1], r.get('customer_name') or '', CJK, shade=row_shade)
        _docx_set_cell(cells[2], str(r.get('customer_phone') or ''), CJK, shade=row_shade)
        _docx_set_cell(cells[3], item_lines, CJK, shade=row_shade)
        _docx_set_cell(cells[4], str(qty), CJK, shade=row_shade)
        _docx_set_cell(cells[5], f"NT$ {amt}", CJK, shade=row_shade)
        _docx_set_cell(cells[6], pay, CJK, shade=row_shade)
        _docx_set_cell(cells[7], remarks, CJK, shade=row_shade)
        _docx_set_cell(cells[8], status_txt, CJK, shade=row_shade, bold=picked)
```

改成：

```python
        _docx_set_cell(cells[0], str(r.get('order_id') or ''), CJK, shade=row_shade)
        _docx_set_cell(cells[1], r.get('customer_name') or '', CJK, shade=row_shade)
        _docx_set_cell(cells[2], str(r.get('customer_phone') or ''), CJK, shade=row_shade)
        _docx_set_cell(cells[3], pickup_name(r.get('pickup_location')), CJK, shade=row_shade)
        _docx_set_cell(cells[4], item_lines, CJK, shade=row_shade)
        _docx_set_cell(cells[5], str(qty), CJK, shade=row_shade)
        _docx_set_cell(cells[6], f"NT$ {amt}", CJK, shade=row_shade)
        _docx_set_cell(cells[7], pay, CJK, shade=row_shade)
        _docx_set_cell(cells[8], remarks, CJK, shade=row_shade)
        _docx_set_cell(cells[9], status_txt, CJK, shade=row_shade, bold=picked)
```

- [ ] **步驟 4：查詢加欄位並依地點排序**

把 `api_export_shipping` 第 1576–1582 行的 `base_sql`：

```python
        base_sql = """
            SELECT order_id, customer_name, customer_phone, grand_total,
                   order_details, payment_method, total_quantity, is_picked_up
            FROM `Order`
            WHERE order_status='active' AND {date_cond}
            ORDER BY is_picked_up ASC, order_time ASC
        """
```

改成：

```python
        base_sql = """
            SELECT order_id, customer_name, customer_phone, grand_total,
                   order_details, payment_method, payment_status, total_quantity,
                   is_picked_up, pickup_location
            FROM `Order`
            WHERE order_status='active' AND {date_cond}
            ORDER BY pickup_location ASC, is_picked_up ASC, order_time ASC
        """
```

- [ ] **步驟 5：確認 `_PAY_LABELS` 已完全移除**

執行：

```bash
grep -n "_PAY_LABELS" app.py; echo "exit=$? （exit=1 代表已無殘留，正確）"
```

預期：`exit=1`，無輸出。

- [ ] **步驟 6：驗證語法無誤且測試通過**

執行：

```bash
python -c "import ast,sys; ast.parse(open('app.py',encoding='utf-8').read()); print('app.py 語法 OK')" && python -m unittest tests.test_product_fields tests.test_pickup_location tests.test_payment_migration tests.test_notification_payload -v
```

預期：`app.py 語法 OK`，接著 `Ran 33 tests ... OK`

- [ ] **步驟 7：Commit**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
feat: 出貨單加取貨地點欄並依廠區排序

付款欄改印「方式／狀態」兩行；同一廠的訂單排在一起，方便分廠備貨。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 任務 10：顧客端取貨地點選擇

**文件：**
- 修改：`templates/pos.html`（第 205–213 行、第 316–320 行）
- 修改：`static/js/pos.js`（第 393–395、509–518、617–619、634–644、653、688、700–723、929–938 行）
- 修改：`static/css/pos.css`（在第 837 行 `.field-group-h input` 樣式之後追加）

- [ ] **步驟 1：加入 radio 區塊**

在 `templates/pos.html` 的電話欄位 `</div>`（第 210 行）之後、備註欄位 `<div class="field-group-h full">`（第 211 行）之前，插入：

```html
                <div class="field-group-h full">
                  <label>取貨地點</label>
                  <div class="pickup-choice" id="pickup-choice">
                    <label class="pickup-opt">
                      <input type="radio" name="pickup-location" value="tamsui">
                      <span class="pickup-box">
                        <span class="pickup-name">淡水廠</span>
                        <span class="pickup-spot">2 樓大廳</span>
                      </span>
                    </label>
                    <label class="pickup-opt">
                      <input type="radio" name="pickup-location" value="qiyan">
                      <span class="pickup-box">
                        <span class="pickup-name">奇岩廠</span>
                        <span class="pickup-spot">1 樓大廳</span>
                      </span>
                    </label>
                  </div>
                </div>
```

無預設選中值 —— 強迫顧客主動選擇，避免誤送到錯的廠。

- [ ] **步驟 2：訂單成立 modal 加一列**

在 `templates/pos.html` 第 315–318 行的「取件人」那組 `pay-row` 之後、`<div class="pay-row big">` 之前，插入：

```html
      <div class="pay-row">
        <span class="lbl">取貨地點</span>
        <strong id="pay-pickup">—</strong>
      </div>
```

- [ ] **步驟 3：加入 CSS**

在 `static/css/pos.css` 第 837 行起的 `.field-group-h input { ... }` 規則**之後**追加：

```css
/* ===== 取貨地點選擇 ===== */
.pickup-choice {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-top: 6px;
}
.pickup-opt { cursor: pointer; margin: 0; }
.pickup-opt input { position: absolute; opacity: 0; pointer-events: none; }
.pickup-box {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 12px 14px;
  border: 1.5px solid var(--line, #d8d8d0);
  border-radius: 10px;
  background: #fff;
  transition: border-color .15s ease, background .15s ease;
}
.pickup-opt input:checked + .pickup-box {
  border-color: var(--forest, #2f6b3a);
  background: rgba(47, 107, 58, .06);
}
.pickup-opt input:focus-visible + .pickup-box {
  outline: 2px solid var(--forest, #2f6b3a);
  outline-offset: 2px;
}
.pickup-name { font-weight: 600; font-size: 14px; }
.pickup-spot { font-size: 12px; color: var(--ink-soft, #6b6b63); }
@media (max-width: 480px) {
  .pickup-choice { grid-template-columns: 1fr; }
}
```

- [ ] **步驟 4：pos.js 加入取值輔助與地點標籤表**

在 `static/js/pos.js` 的 `function fillPayModal`（第 929 行）**上方**插入：

```js
// 取貨地點（值與 app.py 的 PICKUP_LOCATIONS 一致）
const PICKUP_LABELS = { tamsui: '淡水廠　2 樓大廳', qiyan: '奇岩廠　1 樓大廳' };

function selectedPickup() {
  const el = document.querySelector('input[name="pickup-location"]:checked');
  return el ? el.value : '';
}

function clearPickupChoice() {
  document.querySelectorAll('input[name="pickup-location"]').forEach(r => { r.checked = false; });
}
```

- [ ] **步驟 5：送出鈕連動地點是否已選**

把 `static/js/pos.js` 第 394–395 行：

```js
  const toCk = $('to-checkout-btn'); if (toCk) toCk.disabled = empty;
  const ck = $('checkout-btn'); if (ck) ck.disabled = empty;
```

改成：

```js
  const toCk = $('to-checkout-btn'); if (toCk) toCk.disabled = empty;
  // 取貨地點未選 → 不能送單（與後端 400 一致，先在前端擋下）
  const ck = $('checkout-btn'); if (ck) ck.disabled = empty || !selectedPickup();
```

- [ ] **步驟 6：radio 變動時重算送出鈕狀態**

在 `static/js/pos.js` 第 1044 行 `$('checkout-btn').addEventListener('click', checkout);` **之後**插入：

```js
  document.querySelectorAll('input[name="pickup-location"]').forEach(r => {
    r.addEventListener('change', updateTotals);
  });
```

- [ ] **步驟 7：checkout 帶上地點並在成功後清除**

在 `checkout()` 內，把第 688 行：

```js
  const remarks = $('order-remarks').value.trim();
```

改成：

```js
  const remarks = $('order-remarks').value.trim();
  const pickup = selectedPickup();
  if (!pickup) {
    toast('請選擇取貨地點', 'error');
    return;
  }
```

把第 700–709 行的 fetch body：

```js
      body: JSON.stringify({
        customer_name: name,
        customer_phone: phone,
        order_remarks: remarks,
        packs: state.cart,
      })
```

改成：

```js
      body: JSON.stringify({
        customer_name: name,
        customer_phone: phone,
        order_remarks: remarks,
        pickup_location: pickup,
        packs: state.cart,
      })
```

把第 723 行 `$('order-remarks').value = '';` **之後**插入：

```js
      clearPickupChoice();
```

把第 730 行：

```js
      fillPayModal({ order_id: orderId, name: name, amount: j.grand_total });
```

改成：

```js
      fillPayModal({ order_id: orderId, name: name, amount: j.grand_total, pickup: pickup });
```

- [ ] **步驟 8：fillPayModal 顯示地點**

把 `static/js/pos.js` 第 929–938 行：

```js
function fillPayModal({ order_id, name, amount }) {
  $('pay-order-id').textContent = order_id || '—';
  $('pay-name').textContent = name || '—';
```

改成：

```js
function fillPayModal({ order_id, name, amount, pickup }) {
  $('pay-order-id').textContent = order_id || '—';
  $('pay-name').textContent = name || '—';
  const pickupEl = $('pay-pickup');
  if (pickupEl) pickupEl.textContent = PICKUP_LABELS[pickup] || '—';
```

- [ ] **步驟 9：`serverToCard` 帶上新欄位**

把 `static/js/pos.js` 第 509–518 行：

```js
    status: o.payment_method === 'pending_check' ? 'reported' : 'unpaid',
    payment_method: o.payment_method,
    is_picked_up: !!o.is_picked_up,
    is_notified: !!o.is_notified,
    order_status: o.order_status || 'active',
    ship_date: o.expected_ship_date || '',
```

改成：

```js
    status: o.payment_status === 'pending_check' ? 'reported' : 'unpaid',
    payment_method: o.payment_method,
    payment_status: o.payment_status || 'unpaid',
    is_confirmed: !!o.is_confirmed,
    is_picked_up: !!o.is_picked_up,
    is_notified: !!o.is_notified,
    order_status: o.order_status || 'active',
    ship_date: o.expected_ship_date || '',
    pickup_location: o.pickup_location || '',
```

- [ ] **步驟 10：修正自助取消的判斷條件**

`payment_method` 拆分後不再有 `unpaid` 值，舊條件會永遠為 false，顧客將**再也無法自己取消訂單且沒有任何錯誤提示**。

把 `static/js/pos.js` 第 617–619 行：

```js
    // 準備中 + 還沒被店家「確認訂單」(payment_method=unpaid) → 可自己取消
    let actionsHtml = '';
    const canCancel = (bucket === 'preparing' && o.payment_method === 'unpaid');
```

改成：

```js
    // 準備中 + 還沒被店家「確認訂單」→ 可自己取消
    let actionsHtml = '';
    const canCancel = (bucket === 'preparing' && !o.is_confirmed);
```

同時把第 653 行的註解：

```js
// 用戶取消訂單（店家確認訂單前、payment_method=unpaid 時可取消）
```

改成：

```js
// 用戶取消訂單（店家確認訂單前、is_confirmed=false 時可取消）
```

- [ ] **步驟 11：「我的訂單」卡片顯示取貨地點**

把 `static/js/pos.js` 第 634–639 行卡片內的 `pi-meta` 區塊：

```js
        <div class="pi-meta">
          <div class="pi-oid">#<span class="pi-oid-val"></span></div>
          <div class="pi-name"></div>
        </div>
```

改成：

```js
        <div class="pi-meta">
          <div class="pi-oid">#<span class="pi-oid-val"></span></div>
          <div class="pi-name"></div>
          <div class="pi-pickup"></div>
        </div>
```

並在第 643–644 行的 `textContent` 賦值之後：

```js
    card.querySelector('.pi-oid-val').textContent = o.order_id;
    card.querySelector('.pi-name').textContent = o.name;
```

補上：

```js
    const pickupNode = card.querySelector('.pi-pickup');
    pickupNode.textContent = PICKUP_LABELS[o.pickup_location] || '';
    pickupNode.hidden = !PICKUP_LABELS[o.pickup_location];
```

再於 `static/css/pos.css` 的取貨地點樣式區塊末尾追加：

```css
.pi-pickup {
  font-size: 12px;
  color: var(--ink-soft, #6b6b63);
  margin-top: 2px;
}
```

- [ ] **步驟 12：手動驗收**

啟動服務後開啟點餐頁（`/pos`），逐項確認：

1. 進到「填寫取件資訊」，兩個地點都沒有預設選中，**送出訂單鈕是 disabled**。
2. 點「淡水廠」→ 卡片邊框變綠、送出鈕變成可按。
3. 送出訂單 → 成立 modal 顯示「取貨地點　淡水廠　2 樓大廳」。
4. 關掉 modal 再開一次結帳流程 → 地點已清空、送出鈕重新 disabled。
5. 視窗縮到 480px 以下 → 兩個選項改為上下排列，不會擠壓變形。
6. 用開發者工具把 radio 的 `value` 改成 `taipei` 再送出 → 後端回 400「請選擇取貨地點」，不會建立訂單。
7. 開「我的訂單」抽屜 → 剛下的單顯示「淡水廠　2 樓大廳」，且**有「取消訂單」按鈕**。
8. 後台按「確認訂單」後回到此頁重新整理 → 該筆的「取消訂單」按鈕消失（已接單不能自行取消）。
9. 遷移前的舊訂單（無地點）→ 卡片不顯示地點那一行，也沒有空白間隙。

- [ ] **步驟 13：Commit**

```bash
git add templates/pos.html static/js/pos.js static/css/pos.css
git commit -m "$(cat <<'EOF'
feat: 顧客端下單必選取貨地點

用 radio 而非 select：只有兩個選項，行動裝置上一眼可見，
且無預設值可強迫顧客主動選擇，避免誤送到錯的廠。
訂單成立 modal 與「我的訂單」卡片一併顯示取貨地點與樓層。

自助取消的判斷條件從 payment_method='unpaid' 改為 !is_confirmed —
拆分後 payment_method 不再有 unpaid 值，不改會讓取消功能靜默失效。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 任務 11：後台付款雙下拉與地點欄

**文件：**
- 修改：`templates/admin.html`（第 66–76 行、第 166–178 行）
- 修改：`static/js/admin.js`（第 8、274、285、295、324、392–408、435–436、494–523、535–536 行）
- 修改：`static/js/redeem.js`（第 185–194 行）
- 修改：`static/css/admin.css`（檔案末尾追加）

- [ ] **步驟 1：表頭加欄、colspan 改 11**

把 `templates/admin.html` 第 166–174 行：

```html
              <th>編號 / 時間</th>
              <th>顧客</th>
              <th class="center">數量</th>
              <th class="right">金額</th>
              <th>出貨日</th>
              <th>備註</th>
              <th>收款</th>
              <th class="center">狀態</th>
              <th class="right">操作</th>
```

改成：

```html
              <th>編號 / 時間</th>
              <th>顧客</th>
              <th class="center">數量</th>
              <th class="right">金額</th>
              <th>出貨日</th>
              <th>取貨地點</th>
              <th>備註</th>
              <th>收款</th>
              <th class="center">狀態</th>
              <th class="right">操作</th>
```

把第 178 行的 `colspan="10"` 改成 `colspan="11"`。

- [ ] **步驟 2：側邊欄加地點篩選**

在 `templates/admin.html` 第 66 行 `<div class="footer-toggle">` **之前**插入：

```html
      <div class="pickup-filter">
        <h3>依取貨地點篩選</h3>
        <div class="pickup-filter-btns">
          <button class="pickup-btn active" data-pickup="" type="button">全部</button>
          <button class="pickup-btn" data-pickup="tamsui" type="button">淡水廠</button>
          <button class="pickup-btn" data-pickup="qiyan" type="button">奇岩廠</button>
        </div>
      </div>
```

- [ ] **步驟 3：加入 CSS**

在 `static/css/admin.css` 檔案**末尾**追加：

```css
/* ===== 取貨地點篩選（側邊欄）===== */
.pickup-filter { margin-top: 18px; }
.pickup-filter h3 {
  font-size: 12px;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--ink-soft);
  margin-bottom: 8px;
}
.pickup-filter-btns { display: flex; gap: 6px; flex-wrap: wrap; }
.pickup-btn {
  flex: 1 1 auto;
  padding: 6px 10px;
  font-size: 12.5px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #fff;
  cursor: pointer;
}
.pickup-btn.active {
  background: var(--forest);
  border-color: var(--forest);
  color: #fff;
}

/* ===== 收款：方式 + 狀態 雙下拉 ===== */
.pay-cell { display: flex; flex-direction: column; gap: 4px; }
.pay-cell select {
  font-size: 12px;
  padding: 3px 6px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
}
.pay-cell select.st-paid { background: rgba(47, 107, 58, .10); border-color: var(--forest); }
.pay-cell select.st-pending { background: rgba(200, 150, 30, .12); }

/* ===== 取貨地點下拉（表格內）===== */
select.pickup-sel {
  font-size: 12px;
  padding: 3px 6px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
}
select.pickup-sel.unset { color: var(--ink-soft); font-style: italic; }
```

- [ ] **步驟 4：admin.js 加入地點狀態與篩選按鈕**

把 `static/js/admin.js` 第 8 行：

```js
  payStatus: '',          // '' | 'pending' | 'paid'
```

改成：

```js
  payStatus: '',          // '' | 'pending' | 'paid'
  pickup: '',             // '' | 'tamsui' | 'qiyan'
```

在 `state` 物件的結尾大括號之後、下一個 `function` 之前，加入模組層級常數（放在渲染函式內會每列重建一次）：

```js
// 取貨地點短名（值與 app.py 的 PICKUP_LOCATIONS 一致）
const PICKUP_TEXT = { tamsui: '淡水廠', qiyan: '奇岩廠' };
```

把第 285 行：

```js
    if (state.payStatus) params.set('pay_status', state.payStatus);
```

改成：

```js
    if (state.payStatus) params.set('pay_status', state.payStatus);
    if (state.pickup) params.set('pickup', state.pickup);
```

在第 765 行 `document.querySelectorAll('.pay-tab').forEach(tab => {` 那段的**上方**插入：

```js
  document.querySelectorAll('.pickup-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      state.pickup = btn.dataset.pickup || '';
      document.querySelectorAll('.pickup-btn').forEach(b => {
        b.classList.toggle('active', b === btn);
      });
      loadOrders();
    });
  });
```

- [ ] **步驟 5：colspan 全部改成 11**

把 `static/js/admin.js` 第 274、295、324 行的 `colspan="10"` 全部改成 `colspan="11"`。

執行以下指令確認沒有殘留：

```bash
grep -n 'colspan="10"' static/js/admin.js templates/admin.html; echo "exit=$? （exit=1 代表已全部改完）"
```

預期：`exit=1`，無輸出。

- [ ] **步驟 6：改寫收款欄與新增地點欄的渲染**

把 `static/js/admin.js` 第 392–408 行：

```js
    // Payment select
    const paymentClass = o.payment_method === 'cash' ? 'paid-cash'
                       : o.payment_method === 'transfer' ? 'paid-transfer'
                       : o.payment_method === 'pending_check' ? 'paid-pending' : '';

    // 取消 / 退貨的訂單不顯示「收款下拉」（否則會誤導成「未付款」）→ 改顯示靜態「—」
    let paymentCellHtml;
    if (ost === 'cancelled' || ost === 'refunded') {
      paymentCellHtml = `<span class="payment-void" title="此訂單已${ost === 'cancelled' ? '取消' : '退貨'}，無收款">—</span>`;
    } else {
      paymentCellHtml = `
        <select class="payment ${paymentClass}" data-oid="${esc(o.order_id)}">
          <option value="unpaid"        ${o.payment_method === 'unpaid' ? 'selected' : ''}>未收款</option>
          <option value="pending_check" ${o.payment_method === 'pending_check' ? 'selected' : ''}>⏳ 待對帳</option>
          <option value="cash"          ${o.payment_method === 'cash' ? 'selected' : ''}>現金 ✓</option>
          <option value="transfer"      ${o.payment_method === 'transfer' ? 'selected' : ''}>轉帳 ✓</option>
        </select>`;
    }
```

改成：

```js
    // 收款：方式與狀態各一個下拉（方式＝現金/轉帳；狀態＝未收款/待對帳/已收款）
    const st = o.payment_status || 'unpaid';
    const statusClass = st === 'paid' ? 'st-paid' : st === 'pending_check' ? 'st-pending' : '';

    // 取消 / 退貨的訂單不顯示收款下拉（否則會誤導成「未付款」）→ 改顯示靜態「—」
    let paymentCellHtml;
    if (ost === 'cancelled' || ost === 'refunded') {
      paymentCellHtml = `<span class="payment-void" title="此訂單已${ost === 'cancelled' ? '取消' : '退貨'}，無收款">—</span>`;
    } else {
      paymentCellHtml = `
        <div class="pay-cell">
          <select class="pay-method" data-oid="${esc(o.order_id)}">
            <option value="cash"     ${o.payment_method === 'cash' ? 'selected' : ''}>現金</option>
            <option value="transfer" ${o.payment_method === 'transfer' ? 'selected' : ''}>轉帳</option>
          </select>
          <select class="pay-status ${statusClass}" data-oid="${esc(o.order_id)}">
            <option value="unpaid"        ${st === 'unpaid' ? 'selected' : ''}>未收款</option>
            <option value="pending_check" ${st === 'pending_check' ? 'selected' : ''}>⏳ 待對帳</option>
            <option value="paid"          ${st === 'paid' ? 'selected' : ''}>已收款 ✓</option>
          </select>
        </div>`;
    }

    // 取貨地點：可即時修改；已取貨／已取消的單改成靜態文字
    const loc = o.pickup_location || '';
    let pickupCellHtml;
    if (ost !== 'active' || o.is_picked_up) {
      pickupCellHtml = `<span>${esc(PICKUP_TEXT[loc] || '未指定')}</span>`;
    } else {
      pickupCellHtml = `
        <select class="pickup-sel ${loc ? '' : 'unset'}" data-oid="${esc(o.order_id)}">
          <option value="" ${loc ? '' : 'selected'} disabled>未指定</option>
          <option value="tamsui" ${loc === 'tamsui' ? 'selected' : ''}>淡水廠</option>
          <option value="qiyan"  ${loc === 'qiyan' ? 'selected' : ''}>奇岩廠</option>
        </select>`;
    }
```

- [ ] **步驟 7：在表格列插入地點欄**

把第 430–431 行：

```js
      <td>${shipDateHtml}</td>
      <td><div class="remarks">${remarksHtml}</div></td>
```

改成：

```js
      <td>${shipDateHtml}</td>
      <td>${pickupCellHtml}</td>
      <td><div class="remarks">${remarksHtml}</div></td>
```

- [ ] **步驟 8：改寫按鈕顯示條件**

把第 435–436 行：

```js
          ${ost === 'active' && o.payment_method === 'unpaid' && !o.is_picked_up ? `<button class="btn btn-accent btn-sm" data-action="confirm-order" data-oid="${esc(o.order_id)}" title="確認接單：標記現金 + LINE 通知顧客（帶 QR 來取貨付款）+ 生成 QR">確認訂單</button>` : ''}
          ${ost === 'active' && o.payment_method !== 'unpaid' && !o.is_notified && !o.is_picked_up ? `<button class="btn btn-ghost btn-sm" data-action="notify" data-oid="${esc(o.order_id)}" title="通知顧客可以來取貨了">推播</button>` : ''}
```

改成：

```js
          ${ost === 'active' && !o.is_confirmed && !o.is_picked_up ? `<button class="btn btn-accent btn-sm" data-action="confirm-order" data-oid="${esc(o.order_id)}" title="確認接單：LINE 通知顧客（帶 QR 來取貨付款）+ 生成 QR">確認訂單</button>` : ''}
          ${ost === 'active' && o.is_confirmed && !o.is_notified && !o.is_picked_up ? `<button class="btn btn-ghost btn-sm" data-action="notify" data-oid="${esc(o.order_id)}" title="通知顧客可以來取貨了">推播</button>` : ''}
```

- [ ] **步驟 9：改寫下拉事件處理**

把 `static/js/admin.js` 第 494–523 行整段（`tbody.querySelectorAll('select.payment').forEach(...)`）替換為：

```js
  // 付款方式下拉：只送 method，不動 status
  tbody.querySelectorAll('select.pay-method').forEach(sel => {
    sel.addEventListener('change', async (e) => {
      const oid = e.target.dataset.oid;
      const method = e.target.value;
      const prev = state.orders.find(x => x.order_id === oid)?.payment_method;
      try {
        const r = await authFetch(`/api/orders/${oid}/payment`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ method })
        });
        const j = await r.json();
        if (!j.ok) { e.target.value = prev || 'cash'; toast('更新失敗', 'error'); return; }
        const order = state.orders.find(x => x.order_id === oid);
        if (order) order.payment_method = method;
      } catch {
        e.target.value = prev || 'cash';
        toast('網路錯誤', 'error');
      }
    });
  });

  // 收款狀態下拉：只送 status，不動 method
  tbody.querySelectorAll('select.pay-status').forEach(sel => {
    sel.addEventListener('change', async (e) => {
      const oid = e.target.dataset.oid;
      const status = e.target.value;
      const prev = state.orders.find(x => x.order_id === oid)?.payment_status;
      e.target.className = 'pay-status ' + (
        status === 'paid' ? 'st-paid' : status === 'pending_check' ? 'st-pending' : ''
      );
      try {
        const r = await authFetch(`/api/orders/${oid}/payment`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ status })
        });
        const j = await r.json();
        if (!j.ok) { e.target.value = prev || 'unpaid'; toast('更新失敗', 'error'); return; }
        const order = state.orders.find(x => x.order_id === oid);
        if (order) order.payment_status = status;
        loadPaySummary();
        // 若正在篩選收款狀態，且這筆已不符合條件 → 重新載入讓它離開列表
        if (state.payStatus) {
          const stillMatch = state.payStatus === 'pending'
            ? (status === 'unpaid' || status === 'pending_check')
            : (status === 'paid');
          if (!stillMatch) loadOrders();
        }
      } catch {
        e.target.value = prev || 'unpaid';
        toast('網路錯誤', 'error');
      }
    });
  });

  // 取貨地點下拉
  tbody.querySelectorAll('select.pickup-sel').forEach(sel => {
    sel.addEventListener('change', async (e) => {
      const oid = e.target.dataset.oid;
      const loc = e.target.value;
      const prev = state.orders.find(x => x.order_id === oid)?.pickup_location || '';
      e.target.classList.remove('unset');
      try {
        const r = await authFetch(`/api/orders/${oid}/pickup-location`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ pickup_location: loc })
        });
        const j = await r.json();
        if (!j.ok) { e.target.value = prev; toast(j.error || '更新失敗', 'error'); return; }
        const order = state.orders.find(x => x.order_id === oid);
        if (order) order.pickup_location = loc;
        toast('取貨地點已更新');
        if (state.pickup && state.pickup !== loc) loadOrders();
      } catch {
        e.target.value = prev;
        toast('網路錯誤', 'error');
      }
    });
  });
```

- [ ] **步驟 10：更新「確認訂單」的確認文案**

把第 535–536 行：

```js
        confirmAction('確認訂單', `確認接受訂單 #${oid}？\n將標記為現金、LINE 通知顧客（帶 QR 來取貨付款），並生成 QR。`,
                      () => doConfirmOrder(oid));
```

改成：

```js
        confirmAction('確認訂單', `確認接受訂單 #${oid}？\n將標記為已接單、LINE 通知顧客（帶 QR 來取貨付款），並生成 QR。`,
                      () => doConfirmOrder(oid));
```

- [ ] **步驟 11：修正核銷頁的付款 chip**

`redeem.js` 用 `payment_method === 'unpaid'` 顯示「尚未確認訂單」。拆分後永遠不會成立，未接單的訂單會掉進 else 分支印出原始代碼字串 `cash`。

把 `static/js/redeem.js` 第 185–194 行：

```js
    // 付款方式 chip
    const payNode = el('span');
    if (o.payment_method === 'cash') {
      payNode.appendChild(el('span', 'pay-chip cash', '取貨付現'));
    } else if (o.payment_method === 'unpaid') {
      payNode.appendChild(el('span', 'pay-chip unpaid', '尚未確認訂單'));
    } else {
      payNode.textContent = o.payment_method || '—';
    }
    resultEl.appendChild(sumRow('付款方式', payNode));
```

改成：

```js
    // 付款方式 chip：未接單優先顯示，其次才是方式
    const payNode = el('span');
    if (!o.is_confirmed) {
      payNode.appendChild(el('span', 'pay-chip unpaid', '尚未確認訂單'));
    } else if (o.payment_method === 'cash') {
      payNode.appendChild(el('span', 'pay-chip cash', '取貨付現'));
    } else if (o.payment_method === 'transfer') {
      payNode.appendChild(el('span', 'pay-chip cash', '轉帳'));
    } else {
      payNode.textContent = o.payment_method || '—';
    }
    resultEl.appendChild(sumRow('付款方式', payNode));

    // 取貨地點：讓店員一眼看出客人是不是跑錯廠
    if (o.pickup_label) {
      resultEl.appendChild(sumRow('取貨地點', o.pickup_label));
    }
```

- [ ] **步驟 12：手動驗收**

登入後台後逐項確認：

1. 訂單表格出現「取貨地點」欄，位置在「出貨日」與「備註」之間。
2. 收款欄變成上下兩個下拉：上為方式（現金／轉帳），下為狀態（未收款／待對帳／已收款）。
3. 把某一筆改成「已收款」→ 下拉變綠底，上方「待收款」徽章數字**減一**；重新整理後仍是已收款。
4. 只改方式（現金→轉帳）→ 狀態**不變**；只改狀態 → 方式**不變**。
5. 未接單的訂單顯示「確認訂單」按鈕；按下後該按鈕消失、改出現「推播」。
6. 改某筆的取貨地點 → 跳出「取貨地點已更新」，重新整理後保留。
7. 已取貨的訂單，地點欄是靜態文字、不能改。
8. 側邊欄按「淡水廠」→ 只剩淡水的訂單；按「全部」還原。
9. 匯出出貨單 → Word 內有「取貨地點」欄，付款欄印成「現金／未收款」兩行，同一廠的訂單排在一起。
10. 開核銷頁（`<ADMIN_PATH>/redeem`）掃一張**已接單**訂單的 QR → 付款方式顯示「取貨付現」，並多一列「取貨地點：淡水廠　2 樓大廳」。
11. 掃一張**未接單**訂單的 QR → 付款方式顯示「尚未確認訂單」（不是印出 `cash` 這種原始代碼）。

- [ ] **步驟 13：Commit**

```bash
git add templates/admin.html static/js/admin.js static/css/admin.css static/js/redeem.js
git commit -m "$(cat <<'EOF'
feat: 後台收款改為方式/狀態雙下拉並新增取貨地點欄

操作人員現在可以分別標記「用什麼方式付」與「錢收到沒」，
解決客人早已付現、後台卻只看得到「現金」的問題。
接單判斷改用 is_confirmed，不再依賴 payment_method='unpaid'。
核銷頁一併修正付款 chip 判斷，並顯示取貨地點供店員核對客人是否跑錯廠。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 任務 12：同步部署副本並整體驗收

**文件：**
- 修改：`NAS_Deploy/app/app.py`、`NAS_Deploy/app/templates/`、`NAS_Deploy/app/static/`、`NAS_Deploy/push_server/push_sever.py`

- [ ] **步驟 1：確認 NAS_Deploy 沒有本地獨有的修改**

執行：

```bash
diff app.py NAS_Deploy/app/app.py | grep "^>" | head -30
```

預期：只會看到本次改動之前就存在的落後內容（缺 `nutrition` 欄位那 5 行）。**若出現任何與本次無關的新內容，先停下來詢問使用者**，不要覆蓋。

- [ ] **步驟 2：同步檔案**

執行：

```bash
cp app.py NAS_Deploy/app/app.py && cp push_sever.py NAS_Deploy/push_server/push_sever.py && cp -r templates/. NAS_Deploy/app/templates/ && cp -r static/. NAS_Deploy/app/static/ && echo "同步完成"
```

預期：`同步完成`

- [ ] **步驟 3：驗證同步結果一致**

執行：

```bash
diff app.py NAS_Deploy/app/app.py && diff push_sever.py NAS_Deploy/push_server/push_sever.py && echo "兩份主程式已一致"
```

預期：`兩份主程式已一致`

- [ ] **步驟 4：全部測試通過**

執行：

```bash
python -m unittest tests.test_product_fields tests.test_pickup_location tests.test_payment_migration tests.test_notification_payload -v
```

預期：`Ran 33 tests ... OK`

- [ ] **步驟 5：LINE 通知端對端驗收（需真實 LINE 帳號）**

依序執行並確認收到的訊息：

1. 建一張**淡水**的訂單 → 後台按「確認訂單」→ LINE 收到「下單成功」，內含 `📍 取貨地點：淡水廠　2 樓大廳`。
2. 同一張單按「推播」→ LINE 收到「到貨通知」，標題是**食在好蔬**、含訂單編號、含 `📍 取貨地點：淡水廠　2 樓大廳`。
3. 建一張**奇岩**的訂單，重複上述 → 顯示 `奇岩廠　1 樓大廳`。
4. 找一張遷移前的**舊訂單**（`pickup_location` 為 NULL）按推播 → 訊息正常送出，**沒有** `📍` 段落，也沒有連續空白行。
5. 把一張未接單的訂單方式改成「轉帳」再按「確認訂單」→ 訊息中金額後綴是「（轉帳）」，尾句是「前來取貨。」**不含「付現金」**。

- [ ] **步驟 6：Commit**

```bash
git add NAS_Deploy
git commit -m "$(cat <<'EOF'
chore: 同步 NAS_Deploy 部署副本

app.py、push_sever.py、templates、static 與根目錄一致。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## 上線前提醒

**部署後第一次啟動時，`init_db` 會執行一次性遷移，並在 console 印出 `[init_db] 已執行付款欄位拆分遷移`。**

遷移完成後，原本顯示「現金 ✓（＝已收款）」但**尚未取貨**的訂單會變成「**現金／未收款**」，後台「待收款」數字會一次跳高。這是把「已接單即算已收款」的錯誤統計修正回來，不是新的問題 —— **請事先告知操作人員**，否則他們會以為系統壞了。
