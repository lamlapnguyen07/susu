"""
食在好蔬 — Web 版後端（Flask）
- 登入：帳密由環境變數 ADMIN_USER / ADMIN_PASS 設定（15 分鐘無動作自動登出）
- 資料庫：susu
- 新增欄位：expected_ship_date（預計出貨日期）、total_quantity（總包數）
- 每個 pack 內含 quantity（數量）、line_total（小計 × 數量）
- LINE 通知佇列邏輯完全保留

資訊安全：
  * 密碼比對使用 hmac.compare_digest 防 timing attack
  * Session cookie：HttpOnly + SameSite=Lax；HTTPS 部署時請設環境變數 FLASK_SECURE_COOKIE=1
  * 登入暴力破解限速：同一 IP 10 分鐘內 5 次失敗即鎖
  * CSRF：管理端 state-changing endpoints 需 X-CSRF-Token header
  * 安全標頭：X-Frame-Options / X-Content-Type-Options / Referrer-Policy / CSP
  * 帳密支援 env override：ADMIN_USER / ADMIN_PASS / FLASK_SECRET
"""
import hashlib
import hmac
import json
import os
import secrets
import time

# 自動讀取同目錄的 .env（不會覆蓋已存在的系統環境變數）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # 沒裝 python-dotenv 時靜默略過，仍可用系統 env var
from collections import defaultdict
from datetime import datetime, timedelta, date
from functools import wraps

import mysql.connector
import requests
from mysql.connector import Error, pooling
from flask import (Flask, request, jsonify, render_template,
                   session, redirect, url_for, send_from_directory, abort, send_file)

# ==================== 設定 ====================
# DB 連線：一律讀 env（.env）。不再放任何寫死的帳密 fallback，避免原始碼外洩。
DB_CONFIG = {
    'host':     os.environ.get('DB_HOST', '127.0.0.1'),
    'port':     int(os.environ.get('DB_PORT', '3306')),
    'user':     os.environ.get('DB_USER', ''),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', ''),
    'charset':  'utf8mb4',
}

# 後台帳密：一律讀 env。沒設 = 登入停用（_verify_credentials 會擋），不會留下可外洩的預設密碼。
ADMIN_USER = os.environ.get('ADMIN_USER', '')
ADMIN_PASS = os.environ.get('ADMIN_PASS', '')
# 後台網址路徑：可改成不好猜的秘密路徑，降低被機器人亂掃 /admin 的機會。
# 設環境變數 ADMIN_PATH（例如 ADMIN_PATH=manage-x7k2）；沒設則維持 /admin。
# （這是「多一層」遮蔽，真正的鎖仍是密碼 + 2FA。）
ADMIN_PATH = '/' + os.environ.get('ADMIN_PATH', 'admin').strip().strip('/')
# TOTP 二步驗證密鑰（base32）。未設 = 2FA 關閉（登入照舊）；有設 = 強制 2FA。
# 用 setup_2fa.py 產生後貼進 .env。掉手機 → 改這個值即可重設（永不被鎖死）。
ADMIN_TOTP_SECRET = os.environ.get('ADMIN_TOTP_SECRET', '').strip()
PENDING_2FA_TTL_SEC = 300   # 帳密過了之後，須在 5 分鐘內輸入 6 碼
IDLE_TIMEOUT_MIN = 15

# LINE LIFF / Login 設定（沒設則整段降級為匿名訂購流程，POS 仍可運作）
LIFF_ID = os.environ.get('LIFF_ID', '').strip()
LINE_LOGIN_CHANNEL_ID = os.environ.get('LINE_LOGIN_CHANNEL_ID', '').strip()
LINE_VERIFY_ENDPOINT = 'https://api.line.me/oauth2/v2.1/verify'
LINE_RATE_LIMIT = defaultdict(list)              # ip -> [timestamp,...] for /api/line/verify
LINE_VERIFY_MAX_PER_MIN = 30
LINE_VERIFY_WINDOW_SEC = 60

# 持久化 secret（重啟後 session 不失效）；正式部署請設環境變數
_DEFAULT_SECRET = '_dev-only-secret-DO-NOT-USE-IN-PROD'

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET') or _DEFAULT_SECRET
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=IDLE_TIMEOUT_MIN)
# Cookie 安全旗標
app.config['SESSION_COOKIE_HTTPONLY'] = True       # 阻擋 JS 讀取 cookie
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'      # 阻擋多數 CSRF 場景
# HTTPS 部署時設環境變數 FLASK_SECURE_COOKIE=1 才會啟用，避免本機 http 開發時 session 失效
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_SECURE_COOKIE') == '1'

# 限制單一 request body 上限 1MB — 防巨量 JSON DoS（下單最大也才 50 packs，幾 KB 而已）
app.config['MAX_CONTENT_LENGTH'] = 3 * 1024 * 1024  # 3 MB（含商品圖上傳）


# ==================== 速率限制（登入 + 公開下單） ====================
LOGIN_ATTEMPTS = defaultdict(list)   # ip -> [timestamp,...]
LOGIN_MAX_ATTEMPTS = 3                # 同一 IP 3 次失敗即鎖（只鎖那個 IP，不影響其他網路）
LOGIN_WINDOW_SEC = 3600               # 鎖定 1 小時（每個 IP 各自獨立計算）

ORDER_ATTEMPTS = defaultdict(list)   # ip -> [timestamp,...]
ORDER_MAX_PER_HOUR = int(os.environ.get('ORDER_RATE_LIMIT', '30'))   # 每 IP 每小時最多 N 筆訂單
# 每個 LINE 帳號每天（非取消/退貨）訂單包數加總上限 — 防惡意刷單
DAILY_PACK_LIMIT = int(os.environ.get('DAILY_PACK_LIMIT', '10'))
ORDER_WINDOW_SEC = 3600

# 萵苣包定價（前台顯示與後端計價共用這一份）
COMBO_PACK_PRICE = 200   # 農場搭配包（品種隨機）
PICK_PACK_PRICE = 250    # 指定品種包（最多 3 種偏好）
COMBO_SENTINEL = '組合包（農場隨機搭配）'

# 單件商品（特殊品項 + 合作農場商品）每筆訂單上限
ITEM_MAX_QTY = 20        # 同一品項最多幾件
ITEM_MAX_LINES = 30      # 最多幾個不同品項

# 商品類型：standard = 自家一般萵苣（按包賣）／special = 自家特殊品項（單件）／farm = 合作農場商品（單件）
PRODUCT_KINDS = ('standard', 'special', 'farm')
UNIT_PRICED_KINDS = ('special', 'farm')
# 這週供應狀態（前台顯示成標籤）
STOCK_STATUSES = {'ok': '充足', 'low': '剩少量', 'next': '下週採收'}


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


def _client_ip():
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def _hashed_ip(ip):
    """訪客 IP 做 HMAC-SHA256 後存 — 同 IP 仍會產生同 hash（去重邏輯保留），
    但 DB 被偷時無法反查出真實 IP。salt 用 FLASK_SECRET，重啟不變。"""
    pepper = (app.config.get('SECRET_KEY') or '').encode('utf-8')
    return hmac.new(pepper, (ip or '').encode('utf-8'), hashlib.sha256).hexdigest()[:45]


# ==================== Admin 操作 audit log ====================
# 寫入專屬 audit.log 檔，與一般 print log 分開，方便事故追查
import logging
AUDIT_LOG_PATH = os.environ.get('AUDIT_LOG_PATH', 'audit.log')
_audit_logger = logging.getLogger('admin_audit')
_audit_logger.setLevel(logging.INFO)
_audit_logger.propagate = False
try:
    _fh = logging.FileHandler(AUDIT_LOG_PATH, encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    _audit_logger.addHandler(_fh)
except Exception as _e:
    print(f"[audit] 無法開啟 audit log {AUDIT_LOG_PATH}: {_e}")


def audit(action, **details):
    """記錄 admin 操作到 audit.log。
    範例：audit('login_ok', user='iothouse', ip='1.2.3.4')
         audit('confirm_payment', user='iothouse', ip='1.2.3.4', order_id='...')
    安全：不記錄完整 PII（電話用 mask；只記訂單 ID 而非內容）"""
    try:
        user = session.get('user', '-') if hasattr(request, 'cookies') else '-'
    except Exception:
        user = '-'
    try:
        ip = _client_ip()
    except Exception:
        ip = '-'
    parts = [f"action={action}", f"user={user}", f"ip={ip}"]
    for k, v in details.items():
        parts.append(f"{k}={v}")
    _audit_logger.info(' | '.join(parts))


def _check_login_allowed(ip):
    now = time.time()
    LOGIN_ATTEMPTS[ip] = [t for t in LOGIN_ATTEMPTS[ip] if now - t < LOGIN_WINDOW_SEC]
    return len(LOGIN_ATTEMPTS[ip]) < LOGIN_MAX_ATTEMPTS


def _record_login_fail(ip):
    LOGIN_ATTEMPTS[ip].append(time.time())


def _check_order_rate(ip):
    """公開下單端點速率限制：每 IP 每小時 30 筆"""
    now = time.time()
    ORDER_ATTEMPTS[ip] = [t for t in ORDER_ATTEMPTS[ip] if now - t < ORDER_WINDOW_SEC]
    if len(ORDER_ATTEMPTS[ip]) >= ORDER_MAX_PER_HOUR:
        return False
    ORDER_ATTEMPTS[ip].append(now)
    return True


def _verify_credentials(username, password):
    """timing-safe 比對；先補齊到相同長度避免長度洩漏。
    若 ADMIN_USER / ADMIN_PASS 沒設（空字串）→ 一律拒絕，避免空密碼可登入。"""
    if not ADMIN_USER or not ADMIN_PASS:
        return False
    user_match = hmac.compare_digest(
        (username or '').encode('utf-8'),
        ADMIN_USER.encode('utf-8')
    )
    pass_match = hmac.compare_digest(
        (password or '').encode('utf-8'),
        ADMIN_PASS.encode('utf-8')
    )
    return user_match and pass_match


def _totp_enabled():
    """是否已啟用 2FA（.env 有設 ADMIN_TOTP_SECRET）。"""
    return bool(ADMIN_TOTP_SECRET)


def _verify_totp(code):
    """驗證 6 位 TOTP 碼。容忍 ±30 秒時鐘誤差（valid_window=1）。
    密鑰未設或格式錯一律回 False。"""
    if not ADMIN_TOTP_SECRET:
        return False
    code = (code or '').strip().replace(' ', '')
    if not code.isdigit() or len(code) != 6:
        return False
    try:
        import pyotp
        return pyotp.TOTP(ADMIN_TOTP_SECRET).verify(code, valid_window=1)
    except Exception as e:
        print(f"[totp] verify error: {e}")
        return False


# ==================== CSRF Token ====================
def _get_or_create_csrf():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


def csrf_required(f):
    """state-changing API endpoints 需 X-CSRF-Token header（必須在 login_required 之後）"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        expected = session.get('csrf_token')
        provided = request.headers.get('X-CSRF-Token', '')
        if not expected or not provided or not hmac.compare_digest(provided, expected):
            return jsonify({"error": "csrf_failed"}), 403
        return f(*args, **kwargs)
    return wrapper


# ==================== 安全標頭 ====================
@app.after_request
def add_security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    # camera=(self)：只允許「同源」頁面用相機（核銷頁掃碼需要）；其他來源一律禁止
    resp.headers.setdefault(
        'Permissions-Policy',
        'geolocation=(), microphone=(), camera=(self)'
    )
    # HSTS：告訴瀏覽器之後一律走 HTTPS（只在 HTTPS 環境發送才有意義 — ngrok / 正式部署都會生效）
    # 1 年 + includeSubDomains，符合 HSTS preload list 標準
    if request.is_secure or request.headers.get('X-Forwarded-Proto') == 'https':
        resp.headers.setdefault(
            'Strict-Transport-Security',
            'max-age=31536000; includeSubDomains'
        )
    # 不洩漏 server 版本（預設 Flask/Werkzeug 會送 Server: Werkzeug/X.Y.Z 給攻擊者參考）
    resp.headers['Server'] = 'gv'
    # 動態 HTML 不要被瀏覽器快取（避免改完版面後使用者還看舊版）
    content_type = resp.headers.get('Content-Type', '')
    if 'text/html' in content_type:
        resp.headers['Cache-Control'] = 'no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
    # 寬鬆但合理的 CSP，允許 Google Fonts、LIFF SDK、inline script（templates 內有少量）
    # LIFF SDK 內部會打 api.line.me / access.line.me / gw.line.me / liff.line.me / liffsdk.line-scdn.net 等
    # 圖片走 profile.line-scdn.net / obs.line-scdn.net；用 *.line-scdn.net 跟 *.line.me 一次涵蓋
    csp = (
        "default-src 'self'; "
        "img-src 'self' data: blob: https://*.line-scdn.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "script-src 'self' 'unsafe-inline' https://*.line-scdn.net; "
        "connect-src 'self' https://*.line.me https://*.line-scdn.net; "
        "frame-src https://*.line.me; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    resp.headers.setdefault('Content-Security-Policy', csp)
    return resp


# ==================== DB 工具 ====================
# MySQL 連線池 — 高併發時不會每個 request 都新開一條 TCP
DB_POOL_SIZE = int(os.environ.get('DB_POOL_SIZE', '32'))

try:
    DB_POOL = pooling.MySQLConnectionPool(
        pool_name='goodveggie_pool',
        pool_size=DB_POOL_SIZE,
        pool_reset_session=True,
        **DB_CONFIG
    )
    print(f"[db] 連線池建立成功 (size={DB_POOL_SIZE})")
except Exception as e:
    DB_POOL = None
    print(f"[db] 連線池建立失敗，fallback 到 per-request 連線: {e}")


def get_conn():
    """從連線池取連線；若池不可用就直接新開（保證 fallback）"""
    if DB_POOL is not None:
        try:
            return DB_POOL.get_connection()
        except Exception as e:
            print(f"[db] 從連線池取連線失敗，改直連: {e}")
    return mysql.connector.connect(**DB_CONFIG)


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


def init_db():
    """建立資料表並補上新欄位（idempotent）"""
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `Order` (
                order_id VARCHAR(50) PRIMARY KEY,
                customer_name VARCHAR(100) NOT NULL,
                customer_phone VARCHAR(20) NOT NULL,
                grand_total INT NOT NULL,
                order_details JSON,
                order_time DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for stmt in [
            "ALTER TABLE `Order` ADD COLUMN payment_method VARCHAR(20) DEFAULT 'unpaid'",
            "ALTER TABLE `Order` ADD COLUMN is_notified BOOLEAN DEFAULT FALSE",
            "ALTER TABLE `Order` ADD COLUMN is_picked_up BOOLEAN DEFAULT FALSE",
            "ALTER TABLE `Order` ADD COLUMN line_id VARCHAR(255) DEFAULT NULL",
            "ALTER TABLE `Order` ADD COLUMN expected_ship_date DATE DEFAULT NULL",
            "ALTER TABLE `Order` ADD COLUMN total_quantity INT DEFAULT 0",
            # 訂單狀態機（取消 / 退貨）— 與 payment_method 正交，軟刪除
            "ALTER TABLE `Order` ADD COLUMN order_status VARCHAR(20) DEFAULT 'active'",
            "ALTER TABLE `Order` ADD COLUMN cancelled_at DATETIME DEFAULT NULL",
            "ALTER TABLE `Order` ADD COLUMN cancelled_by VARCHAR(20) DEFAULT NULL",
            "ALTER TABLE `Order` ADD COLUMN refunded_at DATETIME DEFAULT NULL",
            "ALTER TABLE `Order` ADD COLUMN status_note VARCHAR(200) DEFAULT NULL",
            # 訂單 QRCode 驗證碼（確認收款時生成；QR 內容 = order_id|qr_token，供未來核銷防偽）
            "ALTER TABLE `Order` ADD COLUMN qr_token VARCHAR(32) DEFAULT NULL",
            # 核銷時間戳（店員掃碼確認取貨時寫入）
            "ALTER TABLE `Order` ADD COLUMN picked_up_at TIMESTAMP NULL DEFAULT NULL",
            # === 付款三語意拆分 ===
            "ALTER TABLE `Order` ADD COLUMN payment_status VARCHAR(20) DEFAULT 'unpaid'",
            "ALTER TABLE `Order` ADD COLUMN is_confirmed BOOLEAN DEFAULT FALSE",
            # payment_method 語意收窄成「只管方式」，預設值不能再是 'unpaid'
            "ALTER TABLE `Order` MODIFY COLUMN payment_method VARCHAR(20) DEFAULT 'cash'",
            # === 取貨地點 ===
            "ALTER TABLE `Order` ADD COLUMN pickup_location VARCHAR(20) DEFAULT NULL",
        ]:
            try:
                cur.execute(stmt)
            except Exception:
                pass
        # 商品表（CMS 單一資料源；首頁 + 點餐頁都讀它）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Products (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                name_en VARCHAR(100) DEFAULT '',
                category VARCHAR(100) DEFAULT '',
                kind VARCHAR(20) DEFAULT 'standard',
                price INT DEFAULT 0,
                description TEXT,
                features TEXT,
                eating TEXT,
                pairing TEXT,
                nutrition TEXT,
                photo1 VARCHAR(255) DEFAULT '',
                photo2 VARCHAR(255) DEFAULT '',
                is_available BOOLEAN DEFAULT TRUE,
                sort_order INT DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 既有資料庫補欄位（idempotent）
        for stmt in [
            "ALTER TABLE Products ADD COLUMN nutrition TEXT",
            # 多農場市集：NULL = 食在好蔬自家；其他 = Farms.id
            "ALTER TABLE Products ADD COLUMN farm_id INT DEFAULT NULL",
            "ALTER TABLE Products ADD COLUMN unit VARCHAR(10) DEFAULT '包'",
            "ALTER TABLE Products ADD COLUMN stock_status VARCHAR(10) DEFAULT 'ok'",
        ]:
            try:
                cur.execute(stmt)
            except Exception:
                pass
        _seed_products(cur)  # 表為空時才塞入現有 12 款（idempotent）
        # 合作農場（後台「合作農場」頁可自行新增 / 編輯）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Farms (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                region VARCHAR(50) DEFAULT '',
                summary VARCHAR(300) DEFAULT '',
                story TEXT,
                crops VARCHAR(300) DEFAULT '',
                certification VARCHAR(200) DEFAULT '',
                cert_detail VARCHAR(500) DEFAULT '',
                cert_image VARCHAR(255) DEFAULT '',
                logo VARCHAR(255) DEFAULT '',
                logo_mark VARCHAR(255) DEFAULT '',
                gallery TEXT,
                website VARCHAR(255) DEFAULT '',
                photo1 VARCHAR(255) DEFAULT '',
                photo2 VARCHAR(255) DEFAULT '',
                is_active BOOLEAN DEFAULT TRUE,
                sort_order INT DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for stmt in [   # 早期建好的 Farms 表補欄位（idempotent）
            "ALTER TABLE Farms ADD COLUMN cert_detail VARCHAR(500) DEFAULT ''",
            "ALTER TABLE Farms ADD COLUMN cert_image VARCHAR(255) DEFAULT ''",
            "ALTER TABLE Farms ADD COLUMN logo VARCHAR(255) DEFAULT ''",
            "ALTER TABLE Farms ADD COLUMN logo_mark VARCHAR(255) DEFAULT ''",
            "ALTER TABLE Farms ADD COLUMN gallery TEXT",
        ]:
            try:
                cur.execute(stmt)
            except Exception:
                pass
        _seed_farms(cur)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS LineNotificationQueue (
                id INT AUTO_INCREMENT PRIMARY KEY,
                type VARCHAR(20) NOT NULL,
                phone VARCHAR(20) NOT NULL,
                payload JSON,
                status VARCHAR(20) DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 顧客 LINE 綁定（從舊系統移植；若 susu 為新庫先建空表）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS CustomerLineBindings (
                LINE_ID VARCHAR(255) PRIMARY KEY,
                Customer_Name VARCHAR(100),
                Phone_Number VARCHAR(20),
                Register_Time DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_phone (Phone_Number),
                INDEX idx_name  (Customer_Name)
            )
        """)
        # 站點計數（visitor_count 是 distinct IP 累計）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS site_stats (
                k VARCHAR(50) PRIMARY KEY,
                v BIGINT DEFAULT 0
            )
        """)
        cur.execute("""
            INSERT IGNORE INTO site_stats (k, v) VALUES ('visitor_count', 0)
        """)
        # 訪客 IP 去重表（同 IP 只記一次）
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
        cur.close()
        conn.close()
        print("[init_db] OK")
    except Exception as e:
        print(f"[init_db] 資料庫初始化錯誤: {e}")


def enqueue_line_notification(notif_type, phone, payload=None):
    """獨立連線版本：用於後台批次通知 / 後續發送。"""
    try:
        conn = get_conn()
        cur = conn.cursor()
        payload_json = json.dumps(payload, ensure_ascii=False) if payload else None
        cur.execute(
            "INSERT INTO LineNotificationQueue (type, phone, payload) VALUES (%s, %s, %s)",
            (notif_type, phone, payload_json)
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[LINE] 寫入佇列失敗: {e}")
        return False


def _enqueue_line_in_cursor(cur, notif_type, phone, payload=None):
    """共用連線版本：把 LINE 佇列 INSERT 接在現有 cursor 上，省一條連線"""
    try:
        payload_json = json.dumps(payload, ensure_ascii=False) if payload else None
        cur.execute(
            "INSERT INTO LineNotificationQueue (type, phone, payload) VALUES (%s, %s, %s)",
            (notif_type, phone, payload_json)
        )
        return True
    except Exception as e:
        print(f"[LINE inline] {e}")
        return False


# ==================== 訂單 QRCode 生成 ====================
ORDER_QR_DIR = os.environ.get('ORDER_QR_DIR', '訂單')   # 存放成品 QR 圖的資料夾
ORDER_QR_TEMPLATE = os.environ.get('ORDER_QR_TEMPLATE', os.path.join('img', 'QRCODE_main.png'))  # 底圖
ORDER_QR_CENTER = (660, 945)    # QR 中心座標（底圖上）
ORDER_QR_SIZE = 920             # QR 邊長 px
ORDER_QR_TEXT_GAP = 18          # 訂單編號距 QR 底部的間距 px
ORDER_QR_TEXT_SIZE = 72         # 訂單編號字號 px（過長會自動縮小，避免超出卡片框）
ORDER_QR_TEXT_COLOR = (29, 53, 32)  # 訂單編號文字色（深森林綠 #1d3520）

# 公開 QR 圖檔名格式：<order_id>_<qr_token>.png
# order_id 雖可被列舉，但加上 64-bit qr_token 後檔名不可猜 → 防止他人盜看 QR 圖
import re as _re
ORDER_QR_FILENAME_RE = _re.compile(r'^[0-9]{6,50}_[A-Za-z0-9_-]{6,64}\.png$')


def order_qr_filename(order_id, token):
    """成品 QR 圖檔名（不含路徑）。"""
    return f"{order_id}_{token}.png"


def _load_qr_font(size):
    """載入訂單編號用字體（純數字，優先粗體 sans）；找不到就 PIL 預設。"""
    from PIL import ImageFont
    for p in (
        'C:/Windows/Fonts/arialbd.ttf',
        'C:/Windows/Fonts/arial.ttf',
        'C:/Windows/Fonts/msjhbd.ttc',
        'C:/Windows/Fonts/segoeuib.ttf',
    ):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def generate_order_qr(order_id, token):
    """生成「取貨 QR」成品圖：把 QR 疊到底圖 QRCODE_main.png 的中心 (660,800)，
    QR 下方置中畫訂單編號，存到「訂單」資料夾。
    QR 內容 = '<order_id>|<token>'（token 不可猜，供核銷防偽）。
    失敗只記 log 不拋例外（不可影響確認訂單流程）。"""
    try:
        import qrcode
        from PIL import Image, ImageDraw
        os.makedirs(ORDER_QR_DIR, exist_ok=True)

        # 1) 生成 QR（中等容錯，銳利縮放到指定尺寸）
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10, border=1,   # 白邊 quiet zone 縮到 1 個模組（少一點留白）
        )
        qr.add_data(f"{order_id}|{token}")
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        qr_img = qr_img.resize((ORDER_QR_SIZE, ORDER_QR_SIZE), Image.NEAREST)

        # 2) 開底圖（沒有底圖就用白底 1320x1600 fallback）
        if os.path.exists(ORDER_QR_TEMPLATE):
            base = Image.open(ORDER_QR_TEMPLATE).convert("RGB")
        else:
            base = Image.new("RGB", (1320, 1600), "white")

        # 3) 貼 QR（以 ORDER_QR_CENTER 為中心）
        cx, cy = ORDER_QR_CENTER
        qx = cx - ORDER_QR_SIZE // 2
        qy = cy - ORDER_QR_SIZE // 2
        base.paste(qr_img, (qx, qy))

        # 4) QR 下方置中畫訂單編號（字號放大；編號過長則自動縮小，避免超出卡片框）
        draw = ImageDraw.Draw(base)
        text = str(order_id)
        max_w = base.width - 280       # 卡片框內安全寬度（左右各留邊距）
        size = ORDER_QR_TEXT_SIZE
        font = _load_qr_font(size)
        while True:
            try:
                tw = draw.textlength(text, font=font)
            except Exception:
                tw = len(text) * size * 0.55
            if tw <= max_w or size <= 40:
                break
            size -= 4
            font = _load_qr_font(size)
        tx = cx - tw / 2
        ty = qy + ORDER_QR_SIZE + ORDER_QR_TEXT_GAP    # QR 底下留 ORDER_QR_TEXT_GAP px
        draw.text((tx, ty), text, fill=ORDER_QR_TEXT_COLOR, font=font)

        # 5) 存成品（不可猜檔名）
        fname = order_qr_filename(order_id, token)
        base.save(os.path.join(ORDER_QR_DIR, fname))
        return fname
    except ImportError:
        print("[order_qr] 未安裝 qrcode / Pillow，跳過生成。請 pip install qrcode Pillow")
        return None
    except Exception as e:
        print(f"[order_qr] 生成失敗 {order_id}: {e}")
        return None


# ==================== LINE LIFF / ID Token 驗證 ====================
def _check_line_verify_rate(ip):
    """/api/line/verify 限速：每 IP 每分鐘 30 次"""
    now = time.time()
    LINE_RATE_LIMIT[ip] = [t for t in LINE_RATE_LIMIT[ip] if now - t < LINE_VERIFY_WINDOW_SEC]
    if len(LINE_RATE_LIMIT[ip]) >= LINE_VERIFY_MAX_PER_MIN:
        return False
    LINE_RATE_LIMIT[ip].append(now)
    return True


def _verify_line_id_token(id_token):
    """POST 到 LINE 官方 verify endpoint，回傳 claims dict 或 None。
    LINE 端會驗簽章 / exp / aud / iss，我們只要做基本的回應檢查。"""
    if not id_token or not LINE_LOGIN_CHANNEL_ID:
        return None
    try:
        resp = requests.post(
            LINE_VERIFY_ENDPOINT,
            data={'id_token': id_token, 'client_id': LINE_LOGIN_CHANNEL_ID},
            timeout=5,
        )
    except requests.exceptions.RequestException as e:
        print(f"[LINE verify] network error: {e}")
        return None
    if resp.status_code != 200:
        # 只印 status code，不印 body — body 可能含部分 token 或 user identifier
        print(f"[LINE verify] HTTP {resp.status_code} (details omitted)")
        return None
    try:
        claims = resp.json()
    except ValueError:
        return None
    # 額外防禦：再次驗 aud / iss / exp（LINE 已驗過，這是 belt-and-suspenders）
    if str(claims.get('aud', '')) != LINE_LOGIN_CHANNEL_ID:
        return None
    if claims.get('iss') != 'https://access.line.me':
        return None
    try:
        if int(claims.get('exp', 0)) < int(time.time()):
            return None
    except (TypeError, ValueError):
        return None
    if not claims.get('sub'):
        return None
    return claims


# ==================== 認證 ====================
def _touch_session():
    session['last_active'] = datetime.now().isoformat()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('user'):
            if request.path.startswith('/api/'):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for('login_page', next=request.path))
        last = session.get('last_active')
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if datetime.now() - last_dt > timedelta(minutes=IDLE_TIMEOUT_MIN):
                    session.clear()
                    if request.path.startswith('/api/'):
                        return jsonify({"error": "session_expired"}), 401
                    return redirect(url_for('login_page'))
            except Exception:
                pass
        _touch_session()
        return f(*args, **kwargs)
    return wrapper


# ==================== Public Pages ====================
# ==================== 前台商店（多頁、伺服器端渲染，利於搜尋引擎與首屏速度） ====================
def _store_ctx(**extra):
    """所有前台頁共用的資料：商品、農場、定價與取貨規則。"""
    products = storefront_products()
    farms = get_farms()
    farm_by_id = {f['id']: f for f in farms}

    def source_of(p):
        if p['kind'] == 'farm':
            return farm_by_id.get(p['farm_id'], {}).get('name', '合作農場')
        return '食在好蔬'

    # 購物車畫面需要的最小商品資訊（嵌進頁面，購物車不用再打 API）
    catalog = [{
        'id': p['id'], 'name': p['name'], 'kind': p['kind'],
        'price': p['price'] if p['kind'] != 'standard' else PICK_PACK_PRICE,
        'unit': p['unit'] if p['kind'] != 'standard' else '包',
        'photo': (p['photos'] or [''])[0], 'source': source_of(p),
        'available': p['is_available'],
    } for p in products]
    ctx = dict(
        catalog=catalog,
        source_of=source_of,
        products=products,
        farms=farms,
        farm_by_id=farm_by_id,
        lettuces=[p for p in products if p['kind'] == 'standard'],
        specials=[p for p in products if p['kind'] == 'special'],
        farm_goods=[p for p in products if p['kind'] == 'farm'],
        combo_price=COMBO_PACK_PRICE,
        pick_price=PICK_PACK_PRICE,
        combo_sentinel=COMBO_SENTINEL,
        daily_pack_limit=DAILY_PACK_LIMIT,
        item_max_qty=ITEM_MAX_QTY,
        stock_labels=STOCK_STATUSES,
        pickup_locations=PICKUP_LOCATIONS,
    )
    ctx.update(extra)
    return ctx


@app.route('/')
def index():
    """首頁：這週採收 + 兩種買法 + 合作農場"""
    return render_template('store/home.html', **_store_ctx(page='home'))


@app.route('/shop')
def store_shop():
    return render_template('store/shop.html', **_store_ctx(page='shop'))


@app.route('/p/<int:pid>')
def store_product(pid):
    ctx = _store_ctx(page='shop')
    product = next((p for p in ctx['products'] if p['id'] == pid), None)
    if not product:
        abort(404)
    farm = ctx['farm_by_id'].get(product['farm_id']) if product['kind'] == 'farm' else None
    if product['kind'] == 'farm':
        related = [p for p in ctx['farm_goods'] if p['farm_id'] == product['farm_id'] and p['id'] != pid]
    else:
        related = [p for p in ctx['lettuces'] + ctx['specials'] if p['id'] != pid]
    return render_template('store/product.html', product=product, farm=farm,
                           related=related[:4], **ctx)


@app.route('/pack')
def store_pack():
    """農場搭配包（不是 DB 商品，是固定價的「包」）"""
    return render_template('store/pack.html', **_store_ctx(page='shop'))


@app.route('/farms')
def store_farms():
    return render_template('store/farms.html', **_store_ctx(page='farms'))


@app.route('/farms/<int:fid>')
def store_farm(fid):
    ctx = _store_ctx(page='farms')
    farm = ctx['farm_by_id'].get(fid)
    if not farm:
        abort(404)
    goods = [p for p in ctx['farm_goods'] if p['farm_id'] == fid]
    return render_template('store/farm.html', farm=farm, goods=goods, **ctx)


@app.route('/story')
def store_story():
    return render_template('store/story.html', **_store_ctx(page='story'))


@app.route('/partner')
def store_partner():
    """合作方式：寫給農友看的頁面"""
    return render_template('store/partner.html', **_store_ctx(page='partner'))


@app.route('/help')
def store_help():
    return render_template('store/help.html', **_store_ctx(page='help'))


@app.errorhandler(404)
def store_not_found(e):
    # API 與後台維持原本的純文字 404；前台頁給一個能繼續逛的頁面
    if request.path.startswith(('/api/', ADMIN_PATH)):
        return e
    return render_template('store/404.html', **_store_ctx(page='')), 404


@app.route('/pos')
@app.route('/pos/callback')   # LIFF OAuth callback — 兩個 path 都 render 同一頁
def pos_page():
    # CSRF + LIFF_ID 注入；LIFF_ID 為空字串時前端會降級為「只能匿名訂購」
    return render_template('pos.html',
                           liff_id=LIFF_ID,
                           csrf_token=_get_or_create_csrf())


# ===== 靜態資源（外部資料夾） =====
@app.route('/img/<path:filename>')
def serve_img(filename):
    return send_from_directory('img', filename)


@app.route('/farm/<path:filename>')
def serve_farm(filename):
    """場域與菜款圖庫"""
    return send_from_directory('菜場域圖庫', filename)


@app.route('/order-qr/<filename>')
def serve_order_qr(filename):
    """公開提供取貨 QR 成品圖（供 LINE 伺服器抓圖推給顧客）。
    無需登入：保護來自不可猜的檔名 <order_id>_<token>.png。
    嚴格驗證檔名格式（擋路徑穿越 / 列舉）；不提供目錄列表。"""
    if not ORDER_QR_FILENAME_RE.match(filename or ''):
        abort(404)
    resp = send_from_directory(ORDER_QR_DIR, filename)
    # 圖內含 token，僅本人 LINE 訊息知道網址；允許快取但不共用
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    return resp


# ==================== 商品管理（CMS）====================
PRODUCT_IMG_DIR = os.environ.get('PRODUCT_IMG_DIR') or \
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'product-images')
PRODUCT_IMG_RE = _re.compile(r'^[A-Za-z0-9]{6,40}\.jpg$')

# 初始 12 款（表為空時才 seed）：(name, en, category, kind, price, slug, desc, features, eating, pairing)
_PRODUCT_SEED = [
    ("紅奶油萵苣","Red Butter Lettuce","一般水耕萵苣","standard",0,"red-butter","葉片柔嫩肥厚，咬下去帶淡淡奶香尾韻，是水耕萵苣中最受家庭歡迎的入門款。","富含 β-胡蘿蔔素、葉酸與膳食纖維；柔嫩無苦味，小朋友也能輕鬆接受。","以生食最能保留風味與營養。可做沙拉基底、夾入手卷／三明治／墨西哥捲餅；亦可短時間清炒（30 秒內）。","橄欖油＋海鹽即可。進階搭配：藍紋起司、蘋果薄片、烤堅果、油醋醬。"),
    ("綠奶油萵苣","Green Butter Lettuce","一般水耕萵苣","standard",0,"green-butter","口感清爽多汁、纖維細緻、完全無苦味，是萬用沙拉葉。","富含葉酸、維生素 K；質地柔軟適合任何料理。","歐式沙拉、燙青菜、湯品配菜、漢堡夾層；亦適合包小份量沙拉捲。","油醋醬、柑橘類、烤雞、煙燻鮭魚、優格醬。"),
    ("紅橡木萵苣","Red Oak Leaf","一般水耕萵苣","standard",0,"red-oak","深紅色澤帶橡葉造型，視覺與口感兼具的優雅萵苣。","富含花青素與抗氧化營養素；葉緣捲曲層次感極佳。","擺盤裝飾首選、生菜沙拉、墨西哥捲餅外觀加分。","山羊起司、巴薩米克醋、無花果、煙燻肉品。"),
    ("綠橡木萵苣","Green Oak Leaf","一般水耕萵苣","standard",0,"green-oak","經典橡葉造型，溫和的草本香氣，咬感層次分明。","葉片有特殊橡葉形狀；質地介於柔軟與清脆之間。","法式沙拉、三明治、佐餐配菜、輕食碗。","油醋醬、海鮮、烤肉、芥末籽醬。"),
    ("紅火焰萵苣","Red Flame Lettuce","一般水耕萵苣","standard",0,"red-flame","葉緣捲曲帶火焰般的紅暈漸層，富含花青素。","抗氧化力強；色澤艷麗，沙拉視覺主角。","生菜沙拉、捲餅外觀裝飾、Buddha Bowl 主葉。","酸甜醬、烤雞胸、柑橘類、覆盆莓油醋。"),
    ("綠火焰萵苣","Green Flame Lettuce","一般水耕萵苣","standard",0,"green-flame","微微捲曲的葉片，咬感清脆，百搭好食材。","葉片清脆有彈性；接受度高，適合各種料理。","日式沙拉、漢堡夾層、捲餅、肉品包菜。","和風醬、鮪魚、紫蘇、味噌油醋。"),
    ("蘿蔓萵苣","Romaine Lettuce","一般水耕萵苣","standard",0,"romaine","挺立葉片、清脆口感，經典凱薩沙拉的主角。","葉脈粗壯耐嚼；維生素 A、C、K 含量豐富。","凱薩沙拉、烤蘿蔓（直接炙燒）、生菜包肉、義式輕食。","凱薩醬、培根、帕瑪森起司、烤雞、鯷魚。"),
    ("甜蘿蔓萵苣","Sweet Romaine","一般水耕萵苣","standard",0,"sweet-romaine","比一般蘿蔓更甜更嫩的改良品種，連小孩與長輩都喜歡。","天然糖度高、纖維更細緻；幾乎沒有澀味。","直接生食、輕食沙拉、五花肉包菜、生菜壽司。","芝麻醬、五花肉、韓式烤肉、味噌醬。"),
    ("皺葉萵苣","Frilly Leaf","一般水耕萵苣","standard",0,"frilly","細密皺葉造型，是沙拉視覺層次的擔當，咬感清脆。","葉緣高度捲曲，能吸附醬汁；造型獨特。","擺盤裝飾、混合沙拉、義式生菜碗。","油醋醬、起司、堅果、莓果類。"),
    ("甜脆萵苣","Sweet Crisp Lettuce","一般水耕萵苣","standard",0,"sweet-crisp","咬感清脆甜潤，比一般萵苣更耐熱、更適合熟食。","葉柄含水量高；咀嚼後回甘明顯。","生菜沙拉、生菜包肉、輕炒、湯品。","泰式酸辣醬、韓式烤肉、味噌湯、蒜炒。"),
    ("羽衣甘藍","Kale","特殊品項 · 超級食物","special",150,"kale","舉世聞名的超級食物 (Superfood)，營養密度極高。","富含葉黃素、維生素 K、C、A、鈣、鐵；纖維豐厚。","羽衣甘藍脆片（200°C 烤 12 分）、果昔、輕燙、油拌、Buddha Bowl。","橄欖油＋海鹽＋檸檬汁、蘋果、藜麥、酪梨、芒果。"),
    ("芝麻葉","Arugula","特殊品項 · 義式經典","special",150,"arugula","帶獨特芝麻香與微辛辣，是義式料理的靈魂葉菜。","芥末科植物，含硫代葡萄糖苷；風味強烈。","搭義大利麵、披薩 (出爐後撒)、義式起司盤、生牛肉薄片 (Carpaccio)。","帕瑪森起司、橄欖油、檸檬、生火腿、無花果。"),
]


def _seed_products(cur):
    """表為空時才塞入初始 12 款（idempotent）。"""
    try:
        cur.execute("SELECT COUNT(*) FROM Products")
        if (cur.fetchone() or [0])[0]:
            return
        for i, p in enumerate(_PRODUCT_SEED):
            name, en, cat, kind, price, slug, desc, feat, eat, pair = p
            cur.execute(
                "INSERT INTO Products (name,name_en,category,kind,price,description,features,eating,pairing,photo1,photo2,is_available,sort_order)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s)",
                (name, en, cat, kind, price, desc, feat, eat, pair,
                 f"/img/menu/{slug}-1.jpg", f"/img/menu/{slug}-2.jpg", i)
            )
    except Exception as e:
        print(f"[seed_products] {e}")


# 添丁的預設介紹（只在 Farms 表是空的時候寫入；之後在後台改）
TIANDING_SUMMARY = ("桃園大溪的有機菇農場，農業部第四屆百大青農。"
                    "他們用自然栽培種菇，連自己做的黑木耳露、XO 醬都通過有機驗證。")
TIANDING_STORY = (
    "走進添丁的菇寮，香菇、黑木耳、黑美人、白雪菇、紫雲菇、猴頭菇一排排慢慢長大。"
    "這裡堅持自然栽培，不用化學農藥、合成肥料，也不用基因改造技術。\n"
    "採下來的菇，除了新鮮出貨，他們也自己做成加工品：特濃黑木耳露、五福菇 XO 醬、綜合菇脆餅，"
    "還有乾燥的香菇、黑木耳和猴頭菇，自己吃、送人都合適。\n"
    "添丁也把農場打開給大家：菇菇小學堂、採菇體驗、菇包盆栽 DIY，"
    "很多家庭帶著小朋友來，親手認識食物從哪裡來。\n"
    "從菇本身到加工品，都在有機驗證的範圍內，也是農業部選出的第四屆百大青農。"
    "證書就放在下面，每一項都查得到。\n"
    "這也是我們把添丁放上市集的原因：種法透明、經得起看，我們自己也買回家吃。")
# 農場相簿：一行一張「路徑|說明」
TIANDING_GALLERY = ("/img/farms/tianding/farm/greenhouses.jpg|大溪的菇寮\n"
                    "/img/farms/tianding/farm/mushroom-shed.jpg|菇寮裡一排排的菇包\n"
                    "/img/farms/tianding/farm/kid-picking.jpg|小朋友來採菇\n"
                    "/img/farms/tianding/farm/diy-class.jpg|食農教育 DIY 課程")
# 一行一筆「標題：內容」，農場頁會排成表格
TIANDING_CERT_DETAIL = ("證書字號：1-008-240303\n"
                        "驗證機構：采園生態驗證有限公司\n"
                        "驗證品項：蕈菜、自產農產加工品\n"
                        "有效期限：2028 年 4 月 30 日（民國 117 年）")


def _seed_farms(cur):
    """Farms 表為空時塞入目前唯一確定的合作農場（照片請到後台上傳）。idempotent。"""
    try:
        cur.execute("SELECT COUNT(*) FROM Farms")
        if (cur.fetchone() or [0])[0]:
            return
        cur.execute(
            "INSERT INTO Farms (name,region,summary,story,crops,certification,cert_detail,cert_image,"
            "logo,logo_mark,gallery,photo1,photo2,is_active,sort_order) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,0)",
            ("添丁養生黑木耳", "桃園大溪", TIANDING_SUMMARY, TIANDING_STORY,
             "香菇、黑木耳、黑美人、白雪菇、紫雲菇、猴頭菇",
             "有機驗證 1-008-240303",
             TIANDING_CERT_DETAIL,
             "/img/farms/tianding/organic-cert.jpg",
             "/img/farms/tianding/logo.png",
             "/img/farms/tianding/logo-mark.png",
             TIANDING_GALLERY,
             "/img/farms/tianding/fresh-fungus.jpg",
             "/img/farms/tianding/farm/mushroom-shed.jpg"))
    except Exception as e:
        print(f"[seed_farms] {e}")


def _product_to_dict(r):
    photos = [p for p in (r.get('photo1'), r.get('photo2')) if p]
    kind = r.get("kind") or "standard"
    if kind not in PRODUCT_KINDS:
        kind = "standard"
    stock = r.get("stock_status") or "ok"
    if stock not in STOCK_STATUSES:
        stock = "ok"
    farm_id = r.get("farm_id")
    return {
        "id": r["id"], "name": r["name"], "name_en": r.get("name_en") or "",
        "category": r.get("category") or "", "kind": kind,
        "price": int(r.get("price") or 0),
        "unit": (r.get("unit") or "包"),
        "farm_id": int(farm_id) if farm_id else None,
        "stock_status": stock,
        "description": r.get("description") or "", "features": r.get("features") or "",
        "nutrition": r.get("nutrition") or "",
        "eating": r.get("eating") or "", "pairing": r.get("pairing") or "",
        "photos": photos, "is_available": bool(r.get("is_available")),
    }


def _parse_gallery(text):
    """「路徑|說明」一行一張 → [{src, caption}]；只接受站內路徑（/ 開頭），擋外部連結。"""
    out = []
    for line in (text or '').splitlines():
        src, _, cap = line.partition('|')
        src = src.strip()
        if src.startswith('/') and not src.startswith('//'):
            out.append({"src": src, "caption": cap.strip()})
    return out[:12]


def _farm_to_dict(r):
    photos = [p for p in (r.get('photo1'), r.get('photo2')) if p]
    return {
        "id": r["id"], "name": r["name"], "region": r.get("region") or "",
        "summary": r.get("summary") or "", "story": r.get("story") or "",
        "crops": r.get("crops") or "", "certification": r.get("certification") or "",
        "cert_detail": r.get("cert_detail") or "", "cert_image": r.get("cert_image") or "",
        "logo": r.get("logo") or "", "logo_mark": r.get("logo_mark") or "",
        "gallery": _parse_gallery(r.get("gallery")),
        "website": r.get("website") or "", "photos": photos,
        "is_active": bool(r.get("is_active")), "sort_order": int(r.get("sort_order") or 0),
    }


def _read_product_fields(data):
    """從 JSON 取出/清洗商品欄位。"""
    kind = data.get('kind') if data.get('kind') in PRODUCT_KINDS else 'standard'
    def s(k, n):
        return (str(data.get(k) or '')).strip()[:n]
    try:
        price = max(0, min(100000, int(data.get('price') or 0)))
    except (TypeError, ValueError):
        price = 0
    try:
        sort_order = int(data.get('sort_order') or 0)
    except (TypeError, ValueError):
        sort_order = 0
    # 合作農場商品必須掛在某個農場；自家商品（standard / special）一律不掛
    farm_id = None
    if kind == 'farm':
        try:
            farm_id = int(data.get('farm_id') or 0) or None
        except (TypeError, ValueError):
            farm_id = None
    stock = data.get('stock_status') if data.get('stock_status') in STOCK_STATUSES else 'ok'
    return {
        'name': s('name', 100), 'name_en': s('name_en', 100), 'category': s('category', 100),
        'kind': kind, 'price': price if kind in UNIT_PRICED_KINDS else 0,
        'unit': s('unit', 10) or '包',
        'farm_id': farm_id, 'stock_status': stock,
        'description': s('description', 2000), 'features': s('features', 2000),
        'nutrition': s('nutrition', 2000),
        'eating': s('eating', 2000), 'pairing': s('pairing', 2000),
        'is_available': 1 if data.get('is_available', True) else 0,
        'sort_order': sort_order,
    }


def _read_farm_fields(data):
    """從 JSON 取出/清洗農場欄位。"""
    def s(k, n):
        return (str(data.get(k) or '')).strip()[:n]
    try:
        sort_order = int(data.get('sort_order') or 0)
    except (TypeError, ValueError):
        sort_order = 0
    website = s('website', 255)
    if website and not website.lower().startswith(('http://', 'https://')):
        website = ''   # 只接受 http(s)，擋 javascript: 之類
    return {
        'name': s('name', 100), 'region': s('region', 50), 'summary': s('summary', 300),
        'story': s('story', 5000), 'crops': s('crops', 300),
        'certification': s('certification', 200), 'website': website,
        'is_active': 1 if data.get('is_active', True) else 0,
        'sort_order': sort_order,
    }


def _save_product_image(file_storage):
    """驗證並重新編碼商品圖（去掉惡意內容）→ 存成隨機檔名 JPEG，回傳公開路徑。"""
    import io as _io
    from PIL import Image
    raw = file_storage.read()
    if not raw or len(raw) > 3 * 1024 * 1024:
        raise ValueError("圖片過大或為空")
    img = Image.open(_io.BytesIO(raw))    # 不是真圖會丟例外
    img = img.convert('RGB')
    img.thumbnail((1200, 1200))           # 限制尺寸
    os.makedirs(PRODUCT_IMG_DIR, exist_ok=True)
    fname = (secrets.token_hex(10) + '.jpg')
    img.save(os.path.join(PRODUCT_IMG_DIR, fname), 'JPEG', quality=85)
    return '/product-img/' + fname


@app.route('/product-img/<filename>')
def serve_product_img(filename):
    """公開提供商品圖（CMS 上傳的圖）。檔名嚴格驗證擋穿越。"""
    if not PRODUCT_IMG_RE.match(filename or ''):
        abort(404)
    resp = send_from_directory(PRODUCT_IMG_DIR, filename)
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp


def _load_special_prices():
    """讀取目前『供應中』的特殊品項價格 {name: price}。
    DB 讀不到回 None → 下單端會退回信任前端價，避免整個下單壞掉。"""
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT name, price FROM Products WHERE kind='special' AND is_available=TRUE")
        m = {r['name']: int(r['price'] or 0) for r in cur.fetchall()}
        cur.close()
        conn.close()
        return m
    except Exception as e:
        print(f"[special_prices] {e}")
        return None


# /api/products 記憶體快取：首頁+點餐頁每次載入都會打，商品很少變動 → 快取 30 秒省 DB。
_PRODUCTS_CACHE = {"data": None, "ts": 0.0}
_PRODUCTS_CACHE_TTL = 30  # 秒


def _invalidate_products_cache():
    """後台改了商品後呼叫，讓公開 API 立刻反映（不用等 30 秒）。"""
    _PRODUCTS_CACHE["data"] = None
    _PRODUCTS_CACHE["ts"] = 0.0


def get_products():
    """全部商品（含暫無供應），30 秒快取。DB 掛掉 → 有舊快取回舊的，否則空清單（前台不整頁壞）。"""
    now = time.time()
    if _PRODUCTS_CACHE["data"] is not None and (now - _PRODUCTS_CACHE["ts"]) < _PRODUCTS_CACHE_TTL:
        return _PRODUCTS_CACHE["data"]
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM Products ORDER BY sort_order ASC, id ASC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        data = [_product_to_dict(r) for r in rows]
        _PRODUCTS_CACHE["data"] = data
        _PRODUCTS_CACHE["ts"] = now
        return data
    except Exception as e:
        print(f"[get_products] {e}")
        return _PRODUCTS_CACHE["data"] or []


_FARMS_CACHE = {"data": None, "ts": 0.0}


def _invalidate_farms_cache():
    _FARMS_CACHE["data"] = None
    _FARMS_CACHE["ts"] = 0.0


def get_farms(include_inactive=False):
    """合作農場清單（預設只回上架中的），30 秒快取。"""
    now = time.time()
    data = _FARMS_CACHE["data"]
    if data is None or (now - _FARMS_CACHE["ts"]) >= _PRODUCTS_CACHE_TTL:
        try:
            conn = get_conn()
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM Farms ORDER BY sort_order ASC, id ASC")
            rows = cur.fetchall()
            cur.close()
            conn.close()
            data = [_farm_to_dict(r) for r in rows]
            _FARMS_CACHE["data"] = data
            _FARMS_CACHE["ts"] = now
        except Exception as e:
            print(f"[get_farms] {e}")
            data = _FARMS_CACHE["data"] or []
    return data if include_inactive else [f for f in data if f["is_active"]]


def storefront_products():
    """前台可見的商品：合作農場商品只顯示「農場上架中」的。"""
    active = {f["id"] for f in get_farms()}
    return [p for p in get_products()
            if p["kind"] != "farm" or (p["farm_id"] in active)]


@app.route('/api/products')
def api_products():
    """公開：商品清單（首頁 + 點餐頁共用）。含 is_available 旗標（暫不提供仍回傳）。"""
    return jsonify(get_products())


@app.route('/api/farms')
def api_farms():
    """公開：上架中的合作農場。"""
    return jsonify(get_farms())


@app.route(ADMIN_PATH + '/products')
@login_required
def admin_products_page():
    return render_template('admin_products.html', user=session.get('user'),
                           idle_timeout_min=IDLE_TIMEOUT_MIN, csrf_token=_get_or_create_csrf())


@app.route('/api/admin/products', methods=['POST'])
@login_required
@csrf_required
def api_admin_product_create():
    f = _read_product_fields(request.get_json(silent=True) or {})
    if not f['name']:
        return jsonify({"ok": False, "error": "請填商品名稱"}), 400
    if f['kind'] == 'farm' and not f['farm_id']:
        return jsonify({"ok": False, "error": "合作農場商品請選擇所屬農場"}), 400
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO Products (name,name_en,category,kind,price,unit,farm_id,stock_status,"
            "description,features,nutrition,eating,pairing,is_available,sort_order)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (f['name'], f['name_en'], f['category'], f['kind'], f['price'],
             f['unit'], f['farm_id'], f['stock_status'],
             f['description'], f['features'], f['nutrition'], f['eating'], f['pairing'], f['is_available'], f['sort_order'])
        )
        pid = cur.lastrowid
        conn.commit()
        cur.close()
        conn.close()
        audit('product_create', pid=pid, name=f['name'])
        _invalidate_products_cache()
        return jsonify({"ok": True, "id": pid})
    except Exception as e:
        print(f"[product_create] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500


@app.route('/api/admin/products/<int:pid>', methods=['POST'])
@login_required
@csrf_required
def api_admin_product_update(pid):
    f = _read_product_fields(request.get_json(silent=True) or {})
    if not f['name']:
        return jsonify({"ok": False, "error": "請填商品名稱"}), 400
    if f['kind'] == 'farm' and not f['farm_id']:
        return jsonify({"ok": False, "error": "合作農場商品請選擇所屬農場"}), 400
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE Products SET name=%s,name_en=%s,category=%s,kind=%s,price=%s,unit=%s,farm_id=%s,stock_status=%s,"
            "description=%s,features=%s,nutrition=%s,eating=%s,pairing=%s,is_available=%s,sort_order=%s WHERE id=%s",
            (f['name'], f['name_en'], f['category'], f['kind'], f['price'],
             f['unit'], f['farm_id'], f['stock_status'],
             f['description'], f['features'], f['nutrition'], f['eating'], f['pairing'], f['is_available'], f['sort_order'], pid)
        )
        conn.commit()
        cur.close()
        conn.close()
        audit('product_update', pid=pid)
        _invalidate_products_cache()
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[product_update] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500


@app.route('/api/admin/products/<int:pid>/delete', methods=['POST'])
@login_required
@csrf_required
def api_admin_product_delete(pid):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM Products WHERE id=%s", (pid,))
        conn.commit()
        cur.close()
        conn.close()
        audit('product_delete', pid=pid)
        _invalidate_products_cache()
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[product_delete] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500


@app.route('/api/admin/products/<int:pid>/image', methods=['POST'])
@login_required
@csrf_required
def api_admin_product_image(pid):
    slot = request.form.get('slot')
    if slot not in ('1', '2'):
        return jsonify({"ok": False, "error": "slot 須為 1 或 2"}), 400
    file = request.files.get('image')
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "沒有選擇檔案"}), 400
    try:
        url = _save_product_image(file)
    except Exception:
        return jsonify({"ok": False, "error": "圖片無效或過大（僅接受圖片、≤3MB）"}), 400
    try:
        conn = get_conn()
        cur = conn.cursor()
        col = 'photo1' if slot == '1' else 'photo2'
        cur.execute(f"UPDATE Products SET {col}=%s WHERE id=%s", (url, pid))
        conn.commit()
        cur.close()
        conn.close()
        audit('product_image', pid=pid, slot=slot)
        _invalidate_products_cache()
        return jsonify({"ok": True, "url": url})
    except Exception as e:
        print(f"[product_image] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500


# ==================== 合作農場管理（後台） ====================
@app.route(ADMIN_PATH + '/farms')
@login_required
def admin_farms_page():
    return render_template('admin_farms.html', user=session.get('user'),
                           idle_timeout_min=IDLE_TIMEOUT_MIN, csrf_token=_get_or_create_csrf())


@app.route('/api/admin/farms')
@login_required
def api_admin_farms():
    """後台：全部農場（含下架中）。"""
    _invalidate_farms_cache()
    return jsonify(get_farms(include_inactive=True))


def _farm_write(sql, params, action, **audit_kw):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(sql, params)
        new_id = cur.lastrowid
        conn.commit()
        cur.close()
        conn.close()
        audit(action, **audit_kw)
        _invalidate_farms_cache()
        _invalidate_products_cache()
        return new_id
    except Exception as e:
        print(f"[{action}] {e}")
        return None


@app.route('/api/admin/farms', methods=['POST'])
@login_required
@csrf_required
def api_admin_farm_create():
    f = _read_farm_fields(request.get_json(silent=True) or {})
    if not f['name']:
        return jsonify({"ok": False, "error": "請填農場名稱"}), 400
    fid = _farm_write(
        "INSERT INTO Farms (name,region,summary,story,crops,certification,website,is_active,sort_order)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (f['name'], f['region'], f['summary'], f['story'], f['crops'], f['certification'],
         f['website'], f['is_active'], f['sort_order']),
        'farm_create', name=f['name'])
    if fid is None:
        return jsonify({"ok": False, "error": "server_error"}), 500
    return jsonify({"ok": True, "id": fid})


@app.route('/api/admin/farms/<int:fid>', methods=['POST'])
@login_required
@csrf_required
def api_admin_farm_update(fid):
    f = _read_farm_fields(request.get_json(silent=True) or {})
    if not f['name']:
        return jsonify({"ok": False, "error": "請填農場名稱"}), 400
    ok = _farm_write(
        "UPDATE Farms SET name=%s,region=%s,summary=%s,story=%s,crops=%s,certification=%s,"
        "website=%s,is_active=%s,sort_order=%s WHERE id=%s",
        (f['name'], f['region'], f['summary'], f['story'], f['crops'], f['certification'],
         f['website'], f['is_active'], f['sort_order'], fid),
        'farm_update', fid=fid)
    if ok is None:
        return jsonify({"ok": False, "error": "server_error"}), 500
    return jsonify({"ok": True})


@app.route('/api/admin/farms/<int:fid>/delete', methods=['POST'])
@login_required
@csrf_required
def api_admin_farm_delete(fid):
    # 還有商品掛在這個農場 → 不給刪（避免商品變成孤兒）；請先下架或改掛
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM Products WHERE farm_id=%s", (fid,))
        n = (cur.fetchone() or [0])[0]
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[farm_delete] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500
    if n:
        return jsonify({"ok": False,
                        "error": f"這個農場還有 {n} 件商品，請先到「商品管理」刪除或改掛其他農場；只想暫時隱藏可以改成「下架」"}), 400
    ok = _farm_write("DELETE FROM Farms WHERE id=%s", (fid,), 'farm_delete', fid=fid)
    if ok is None:
        return jsonify({"ok": False, "error": "server_error"}), 500
    return jsonify({"ok": True})


@app.route('/api/admin/farms/<int:fid>/image', methods=['POST'])
@login_required
@csrf_required
def api_admin_farm_image(fid):
    slot = request.form.get('slot')
    if slot not in ('1', '2'):
        return jsonify({"ok": False, "error": "slot 須為 1 或 2"}), 400
    file = request.files.get('image')
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "沒有選擇檔案"}), 400
    try:
        url = _save_product_image(file)
    except Exception:
        return jsonify({"ok": False, "error": "圖片無效或過大（僅接受圖片、≤3MB）"}), 400
    col = 'photo1' if slot == '1' else 'photo2'
    ok = _farm_write(f"UPDATE Farms SET {col}=%s WHERE id=%s", (url, fid),
                     'farm_image', fid=fid, slot=slot)
    if ok is None:
        return jsonify({"ok": False, "error": "server_error"}), 500
    return jsonify({"ok": True, "url": url})


@app.route('/login', methods=['GET'])
def login_page():
    if session.get('user'):
        return redirect(url_for('admin_page'))
    return render_template('login.html')


@app.route(ADMIN_PATH)
@login_required
def admin_page():
    return render_template('admin.html', user=session.get('user'),
                           idle_timeout_min=IDLE_TIMEOUT_MIN,
                           csrf_token=_get_or_create_csrf())


# ==================== Auth API ====================
@app.route('/api/login', methods=['POST'])
def api_login():
    ip = _client_ip()
    if not _check_login_allowed(ip):
        return jsonify({
            "ok": False,
            "error": "此 IP 登入失敗次數過多，已暫時鎖定 1 小時後再試"
        }), 429

    data = request.get_json(silent=True) or {}
    user = (data.get('username') or '').strip()
    pwd = data.get('password') or ''
    # 長度限制：避免 DoS（傳入超大字串）
    if len(user) > 100 or len(pwd) > 200:
        return jsonify({"ok": False, "error": "輸入超出長度"}), 400

    if _verify_credentials(user, pwd):
        if _totp_enabled():
            # 帳密過關，但還沒授權 — 進入第二步：等 6 位 TOTP 碼
            session.clear()                # 防 fixation：先清舊 session
            session['pending_2fa_user'] = user
            session['pending_2fa_ts'] = time.time()
            audit('login_pw_ok_need_2fa', user=user)
            return jsonify({"ok": True, "need_2fa": True})
        # 未啟用 2FA → 維持原本單步登入
        session.clear()                    # 防 session fixation：重新發配
        session.permanent = True
        session['user'] = user
        session['csrf_token'] = secrets.token_urlsafe(32)
        _touch_session()
        LOGIN_ATTEMPTS.pop(ip, None)
        audit('login_ok', user=user)
        return jsonify({"ok": True, "csrf_token": session['csrf_token']})

    _record_login_fail(ip)
    audit('login_fail', user_attempt=user[:32])  # 截斷以免被當 DoS payload 灌爆 log
    # 故意延遲，弱化 timing 攻擊與機器人速度
    time.sleep(0.4)
    return jsonify({"ok": False, "error": "帳號或密碼錯誤"}), 401


@app.route('/api/login/2fa', methods=['POST'])
def api_login_2fa():
    """登入第二步：驗證 6 位 TOTP 碼。需先通過 /api/login（session 有 pending_2fa_user）。"""
    ip = _client_ip()
    if not _check_login_allowed(ip):
        return jsonify({"ok": False, "error": "此 IP 嘗試次數過多，已暫時鎖定 1 小時後再試"}), 429

    pending_user = session.get('pending_2fa_user')
    pending_ts = session.get('pending_2fa_ts', 0)
    # 沒有 pending、或已逾時 → 要求重新登入
    if not pending_user or (time.time() - pending_ts) > PENDING_2FA_TTL_SEC:
        session.clear()
        return jsonify({"ok": False, "error": "驗證逾時，請重新登入", "restart": True}), 401

    data = request.get_json(silent=True) or {}
    code = (data.get('code') or '').strip()
    if len(code) > 10:
        return jsonify({"ok": False, "error": "輸入超出長度"}), 400

    if _verify_totp(code):
        session.clear()                    # 防 fixation：授權前重發 session
        session.permanent = True
        session['user'] = pending_user
        session['csrf_token'] = secrets.token_urlsafe(32)
        _touch_session()
        LOGIN_ATTEMPTS.pop(ip, None)
        audit('login_2fa_ok', user=pending_user)
        return jsonify({"ok": True, "csrf_token": session['csrf_token']})

    _record_login_fail(ip)
    audit('login_2fa_fail', user=pending_user)
    time.sleep(0.4)
    return jsonify({"ok": False, "error": "驗證碼錯誤"}), 401


@app.route('/api/logout', methods=['POST'])
def api_logout():
    user = session.get('user', '-')
    session.clear()
    audit('logout', user=user)
    return jsonify({"ok": True})


@app.route('/api/heartbeat', methods=['POST'])
@login_required
def api_heartbeat():
    """前端定期 ping，順便用來檢查 session 是否還在"""
    return jsonify({"ok": True})


@app.route('/api/visit', methods=['POST'])
def api_visit():
    """訪問計數 — 同 IP 只算一次。回傳目前去重後的訪客總數。
    儲存的是 HMAC-SHA256(IP) 不是原始 IP — 即使 DB 被偷也無法反推使用者 IP。"""
    ip = _client_ip()
    # 限速：跟下單共用 ORDER_ATTEMPTS（30 次/小時/IP）— 同 IP 一小時內不會打超過 30 次正常合理請求
    if not _check_order_rate(ip):
        return jsonify({"count": None}), 429
    try:
        conn = get_conn()
        cur = conn.cursor()
        # 嘗試把 IP hash 寫入；INSERT IGNORE 在重複時 rowcount=0
        cur.execute("INSERT IGNORE INTO site_visitors (ip) VALUES (%s)", (_hashed_ip(ip),))
        new_visitor = cur.rowcount > 0
        if new_visitor:
            cur.execute(
                "INSERT INTO site_stats (k, v) VALUES ('visitor_count', 1) "
                "ON DUPLICATE KEY UPDATE v = v + 1"
            )
        cur.execute("SELECT v FROM site_stats WHERE k='visitor_count'")
        row = cur.fetchone()
        count = int(row[0]) if row else 0
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"count": count, "new": new_visitor})
    except Exception as e:
        print(f"[visit] {e}")
        return jsonify({"count": None}), 200


# ==================== POS APIs ====================
@app.route('/api/customers/search')
@login_required
def api_customer_search():
    """⚠ 已改為 admin-only：原本是公開給 POS autocomplete 用，
    但發現「輸入 09 即可撈整批顧客」的 PII 洩漏 — 改用 LIFF 登入自動帶入個資。
    保留 endpoint 供未來後台搜尋功能使用。
    安全：不回 LINE_ID，僅回 has_line 布林；查詢字必須至少 3 字。"""
    q = (request.args.get('q') or '').strip()
    if len(q) < 3 or len(q) > 30:
        return jsonify([])
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        like = f"%{q}%"
        cur.execute("""
            SELECT Customer_Name AS name, Phone_Number AS phone,
                   (LINE_ID IS NOT NULL AND LINE_ID <> '') AS has_line
            FROM CustomerLineBindings
            WHERE Customer_Name LIKE %s OR Phone_Number LIKE %s
            ORDER BY Customer_Name
            LIMIT 10
        """, (like, like))
        rows = cur.fetchall()
        # MySQL bool returns 0/1; normalise
        for r in rows:
            r['has_line'] = bool(r.get('has_line'))
        cur.close()
        conn.close()
        return jsonify(rows)
    except Exception as e:
        # 不洩漏內部錯誤訊息
        print(f"[customer_search] {e}")
        return jsonify({"error": "search_failed"}), 500


def _price_order_items(raw_items, catalog=None):
    """把前端傳來的 [{product_id, quantity}] 轉成伺服器定價的明細。
    回傳 (items, error)。catalog 預設讀 storefront_products()（測試可注入）。
    同一商品出現多次會合併；下架、暫無供應、非單件商品一律擋下。"""
    if not raw_items:
        return [], None
    if catalog is None:
        catalog = storefront_products()
    by_id = {p['id']: p for p in catalog}
    farms = {f['id']: f['name'] for f in get_farms()} if any(
        p.get('kind') == 'farm' for p in catalog) else {}
    merged = {}
    for it in raw_items:
        if not isinstance(it, dict):
            return None, "購物車內容異常"
        try:
            pid = int(it.get('product_id'))
            qty = int(it.get('quantity', 1))
        except (TypeError, ValueError):
            return None, "購物車內容異常"
        if qty < 1:
            continue
        merged[pid] = merged.get(pid, 0) + qty
    items = []
    for pid, qty in merged.items():
        p = by_id.get(pid)
        if not p or p.get('kind') not in UNIT_PRICED_KINDS:
            return None, "購物車裡有商品已下架，請重新整理頁面"
        if not p.get('is_available'):
            return None, f"「{p['name']}」目前暫無供應，請從購物車移除後再結帳"
        if qty > ITEM_MAX_QTY:
            return None, f"「{p['name']}」每筆訂單最多 {ITEM_MAX_QTY} {p.get('unit') or '件'}"
        price = int(p.get('price') or 0)
        items.append({
            "product_id": pid,
            "name": p['name'],
            "kind": p['kind'],
            "source": farms.get(p.get('farm_id'), '食在好蔬') if p['kind'] == 'farm' else '食在好蔬',
            "unit": p.get('unit') or '件',
            "price": price,
            "quantity": qty,
            "line_total": price * qty,
        })
    return items, None


@app.route('/api/orders', methods=['POST'])
def api_place_order():
    # 公開端點 — 加 IP 速率限制
    ip = _client_ip()
    if not _check_order_rate(ip):
        return jsonify({"ok": False, "error": "下單過於頻繁，請稍後再試"}), 429

    data = request.get_json(silent=True) or {}
    name = (data.get('customer_name') or '').strip()
    phone = (data.get('customer_phone') or '').strip()
    if not name or not phone:
        return jsonify({"ok": False, "error": "缺少姓名或電話"}), 400

    # 輸入長度與格式驗證（防止資料庫汙染、儲存型 XSS、DoS）
    if len(name) > 100 or len(phone) > 20:
        return jsonify({"ok": False, "error": "姓名或電話超出長度"}), 400
    if not phone.replace('-', '').replace(' ', '').isdigit():
        return jsonify({"ok": False, "error": "電話格式錯誤"}), 400
    # 嚴格剔除 HTML 控制字元（額外保險，前端 XSS 仍應 escape）
    if any(c in name for c in '<>"\'`'):
        return jsonify({"ok": False, "error": "姓名含不允許字元"}), 400

    # 取貨地點：必填，且必須是已知代碼（防前端竄改）
    pickup_location = (data.get('pickup_location') or '').strip()
    if not is_valid_pickup(pickup_location):
        return jsonify({"ok": False, "error": "請選擇取貨地點"}), 400

    packs = data.get('packs') or []
    raw_items = data.get('items') or []
    if not isinstance(packs, list) or len(packs) > 50:
        return jsonify({"ok": False, "error": "購物車內容異常"}), 400
    if not isinstance(raw_items, list) or len(raw_items) > ITEM_MAX_LINES:
        return jsonify({"ok": False, "error": "購物車內容異常"}), 400
    if not packs and not raw_items:
        return jsonify({"ok": False, "error": "購物車為空"}), 400

    expected_ship_date = data.get('expected_ship_date')  # YYYY-MM-DD
    order_remarks = (data.get('order_remarks') or '').strip()

    # === 伺服器端重算金額（不信任前端傳來的 subtotal，防止竄改價格）===
    # 規則：組合包/隨機 = COMBO_PACK_PRICE；自選一般萵苣 = PICK_PACK_PRICE；特殊品項加價一律以 DB 現價為準。
    special_prices = _load_special_prices()   # {name: price}；DB 讀不到回 None → 退回信任前端價
    grand_total = 0
    total_quantity = 0
    for p in packs:
        try:
            qty = max(1, min(99, int(p.get('quantity', 1))))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "數量格式錯誤"}), 400

        prefs = p.get('preferences') or []
        is_combo = (not prefs) or (len(prefs) == 1 and prefs[0] == COMBO_SENTINEL)
        base = COMBO_PACK_PRICE if is_combo else PICK_PACK_PRICE

        sp_total = 0
        for sp in (p.get('special_items') or []):
            nm = (sp.get('item_name') or '').strip()
            if special_prices is not None:
                price = special_prices.get(nm)
                if price is None:
                    return jsonify({"ok": False,
                                    "error": f"特殊品項「{nm}」已下架或暫無供應，請重新整理頁面"}), 400
            else:
                try:
                    price = max(0, min(100000, int(sp.get('price', 0))))
                except (TypeError, ValueError):
                    price = 0
            sp['price'] = price          # 一律覆蓋成伺服器算出的價
            sp_total += price

        subtotal = base + sp_total
        p['subtotal'] = subtotal         # 覆蓋前端值，存進 DB 的也是伺服器價
        p['quantity'] = qty
        p['line_total'] = subtotal * qty
        grand_total += p['line_total']
        total_quantity += qty

    # === 單件商品（特殊品項 / 合作農場商品）：只收 product_id + 數量，名稱與價格一律以 DB 為準 ===
    items, err = _price_order_items(raw_items)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    grand_total += sum(it['line_total'] for it in items)

    # order_id 用「秒級時間 + 微秒 + 隨機 3 碼」確保高併發下不撞主鍵
    # order_id：時間戳（含微秒）+ 6 位密碼學隨機 — 防同秒撞主鍵 + 防暴力猜測
    # （原本 3 位隨機 = 1000 種，每秒可暴力測；改 6 位 = 1M 種 + IP rate-limit 30/小時 → 實務上不可猜）
    order_id = (datetime.now().strftime("%y%m%d%H%M%S%f")
                + f"{secrets.randbelow(1000000):06d}")[:50]

    payload = {
        "customer_info": {"name": name, "phone": phone, "remarks": order_remarks},
        "order_details": {
            "order_type": "Combo_Packs_Cart",
            "packs": packs,
            "items": items,
            "grand_total": grand_total,
            "total_quantity": total_quantity,
            "expected_ship_date": expected_ship_date,
        }
    }

    # 一條連線完成：（若有 LIFF 登入）lazy binding → INSERT 訂單 → INSERT LINE 佇列
    # === 會員制（B）：必須已登入 LINE 且已完成會員登記，才能下單 ===
    session_line_id = session.get('line_id') or None
    if not session_line_id:
        return jsonify({"ok": False, "error": "請先用 LINE 登入才能下單"}), 401
    line_id = None
    try:
        conn = get_conn()
        cur = conn.cursor()

        # 查該 LINE 是否已是登記會員；不是 → 擋下，請先到 LINE 機器人「登記」
        cur.execute(
            "SELECT Customer_Name, Phone_Number FROM CustomerLineBindings WHERE LINE_ID = %s",
            (session_line_id,)
        )
        member = cur.fetchone()
        if not member:
            cur.close()
            conn.close()
            return jsonify({
                "ok": False,
                "error": "您尚未完成會員登記，請先到我們的 LINE 輸入「登記」綁定後再下單"
            }), 403

        # 已是會員：一律以「會員登記的姓名 / 電話」下單（不採用前端傳值，避免冒用他人身分）
        name = member[0]
        phone = member[1]
        line_id = session_line_id

        # === 每日上限：同一帳號當天（非取消/退貨）訂單包數加總不得超過上限，防惡意刷單 ===
        cur.execute(
            "SELECT COALESCE(SUM(total_quantity), 0) FROM `Order` "
            "WHERE line_id = %s AND order_status = 'active' AND DATE(order_time) = CURDATE()",
            (line_id,)
        )
        today_packs = int((cur.fetchone() or [0])[0] or 0)
        if today_packs + total_quantity > DAILY_PACK_LIMIT:
            cur.close()
            conn.close()
            remaining = max(0, DAILY_PACK_LIMIT - today_packs)
            if remaining <= 0:
                msg = f"每個帳號每天最多訂購 {DAILY_PACK_LIMIT} 包，您今天已達上限，請明天再下單。"
            else:
                msg = (f"每個帳號每天最多訂購 {DAILY_PACK_LIMIT} 包；"
                       f"您今天已訂 {today_packs} 包，最多再訂 {remaining} 包。")
            audit('order_daily_limit', line_id=str(line_id)[:8], today=today_packs, want=total_quantity)
            return jsonify({"ok": False, "error": msg,
                            "daily_limit": True, "remaining": remaining}), 400

        # 2) INSERT 訂單
        cur.execute("""
            INSERT INTO `Order`
            (order_id, customer_name, customer_phone, grand_total, order_details,
             payment_method, payment_status, is_confirmed, is_notified, is_picked_up,
             line_id, expected_ship_date, total_quantity, pickup_location)
            VALUES (%s, %s, %s, %s, %s, 'cash', 'unpaid', FALSE, FALSE, FALSE, %s, %s, %s, %s)
        """, (order_id, name, phone, grand_total,
              json.dumps(payload, ensure_ascii=False),
              line_id, expected_ship_date, total_quantity, pickup_location))

        # 注意：下單時「不」推 LINE 通知。
        # 推播只在以下兩個時機觸發：
        #   (1) 後台「✓ 收款」按鈕         → payment_confirmed（自動）
        #   (2) 後台「推播」按鈕           → order_arrived  （手動，貨到通知）
        conn.commit()
        cur.close()
        conn.close()
    except Error as e:
        print(f"[place_order] DB error: {e}")
        return jsonify({"ok": False, "error": "下單失敗，請稍後再試"}), 500

    return jsonify({"ok": True, "order_id": order_id,
                    "grand_total": grand_total,
                    "total_quantity": total_quantity})


# ==================== Admin APIs ====================
@app.route('/api/orders')
@login_required
def api_list_orders():
    q = (request.args.get('q') or '').strip()                  # 搜尋：姓名 / 電話 / 訂單編號
    show_closed = request.args.get('show_closed') == '1'
    show_voided = request.args.get('show_voided') == '1'   # 是否顯示已取消/已退貨
    ship_date = (request.args.get('ship_date') or '').strip()  # YYYY-MM-DD or 'none' or ''
    pay_status = (request.args.get('pay_status') or '').strip()  # '', 'pending', 'unpaid', 'pending_check', 'paid'
    pickup = (request.args.get('pickup') or '').strip()          # '' | 'tamsui' | 'qiyan'
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        sql = "SELECT * FROM `Order`"
        cond = []
        params = []
        # 預設只看正常單（active）；勾「顯示已取消/退貨」才放行 cancelled/refunded
        if not show_voided:
            cond.append("order_status = 'active'")
        if q:
            # 搜尋模式：跨所有日期、含已結案，用 姓名 / 電話 / 訂單編號 模糊比對
            like = f"%{q[:30]}%"
            cond.append("(customer_name LIKE %s OR customer_phone LIKE %s OR order_id LIKE %s)")
            params.extend([like, like, like])
        else:
            # 一般模式：日期 / 已結案 / 付款 篩選
            if not show_closed:
                cond.append("is_picked_up = FALSE")
            if ship_date == 'none':
                cond.append("expected_ship_date IS NULL")
            elif ship_date:
                cond.append("expected_ship_date = %s")
                params.append(ship_date)
            # 付款狀態篩選
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
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY expected_ship_date ASC, order_time DESC"
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        for r in rows:
            r['order_time'] = r['order_time'].isoformat(sep=' ') if r['order_time'] else None
            r['expected_ship_date'] = r['expected_ship_date'].isoformat() if r['expected_ship_date'] else None
            # 取消/退貨時間戳序列化（避免 jsonify 失敗）
            for tk in ('cancelled_at', 'refunded_at'):
                if r.get(tk) and isinstance(r[tk], (datetime, date)):
                    r[tk] = r[tk].isoformat(sep=' ')
            try:
                r['order_details'] = json.loads(r['order_details']) if r['order_details'] else {}
            except Exception:
                r['order_details'] = {}
        cur.close()
        conn.close()
        return jsonify(rows)
    except Exception as e:
        print(f"[list_orders] {e}")
        return jsonify({"error": "server_error"}), 500


@app.route('/api/orders/ship-dates')
@login_required
def api_ship_dates():
    """依出貨日期 groupby，回傳每天的訂單統計"""
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT expected_ship_date,
                   COUNT(*) AS total,
                   SUM(CASE WHEN is_notified=0 AND is_picked_up=0 THEN 1 ELSE 0 END) AS unnotified,
                   SUM(CASE WHEN is_picked_up=0 THEN 1 ELSE 0 END) AS unpicked,
                   SUM(CASE WHEN is_picked_up=1 THEN 1 ELSE 0 END) AS picked
            FROM `Order`
            GROUP BY expected_ship_date
            ORDER BY expected_ship_date IS NULL, expected_ship_date ASC
        """)
        rows = cur.fetchall()
        result = []
        for r in rows:
            d = r['expected_ship_date']
            result.append({
                'date': d.isoformat() if d else None,
                'total': int(r['total'] or 0),
                'unnotified': int(r['unnotified'] or 0),
                'unpicked': int(r['unpicked'] or 0),
                'picked': int(r['picked'] or 0),
            })
        cur.close()
        conn.close()
        return jsonify(result)
    except Exception as e:
        print(f"[ship_dates] {e}")
        return jsonify({"error": "server_error"}), 500


@app.route('/api/orders/pay-summary')
@login_required
def api_pay_summary():
    """付款狀態統計：尚未收款（未付款 + 待對帳）的訂單數，僅統計未取貨訂單"""
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT
                SUM(CASE WHEN payment_status='unpaid' THEN 1 ELSE 0 END) AS unpaid,
                SUM(CASE WHEN payment_status='pending_check' THEN 1 ELSE 0 END) AS pending_check,
                SUM(CASE WHEN payment_status='paid' THEN 1 ELSE 0 END) AS paid
            FROM `Order`
            WHERE is_picked_up = FALSE
        """)
        r = cur.fetchone() or {}
        cur.close()
        conn.close()
        unpaid = int(r.get('unpaid') or 0)
        pending_check = int(r.get('pending_check') or 0)
        return jsonify({
            'unpaid': unpaid,
            'pending_check': pending_check,
            'paid': int(r.get('paid') or 0),
            'pending_total': unpaid + pending_check,
        })
    except Exception as e:
        print(f"[pay_summary] {e}")
        return jsonify({"error": "server_error"}), 500


# ==================== 出貨單匯出（Word / .docx）====================
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


def _format_ship_date_zh(date_str):
    """'2026-06-09' → '2026/06/09（週二）'；失敗回原字串。"""
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d')
        return f"{date_str.replace('-', '/')}（週{'一二三四五六日'[d.weekday()]}）"
    except Exception:
        return date_str


def _packs_to_lines(packs):
    """把一張訂單的 packs 壓成多行字串（每包一行）。沿用後台顯示邏輯。"""
    lines = []
    for i, p in enumerate(packs or []):
        bits = []
        prefs = p.get('preferences') or []
        if prefs and prefs[0] != '農場隨機搭配':
            bits.append('、'.join(str(x) for x in prefs))
        sp = p.get('special_items') or []
        if sp:
            bits.append('特殊：' + '、'.join(s.get('item_name', '') for s in sp))
        pr = p.get('pack_remarks')
        if pr and pr != '無':
            bits.append(str(pr))
        qtym = f"×{p.get('quantity')}" if (p.get('quantity') or 1) > 1 else ''
        lines.append(f"[包{i+1}{qtym}] " + (' ｜ '.join(bits) if bits else '農場隨機搭配'))
    return lines


def _items_to_lines(items):
    """單件商品（特殊品項 / 合作農場）壓成多行字串：[來源] 名稱 ×數量。"""
    lines = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        src = it.get('source') or ''
        qty = it.get('quantity') or 1
        unit = it.get('unit') or ''
        lines.append(f"[{src}] {it.get('name', '')} ×{qty}{unit}")
    return lines


def _docx_set_cell(cell, lines, cjk_font, bold=False, shade=None, size=9):
    """寫入儲存格（支援多行）並套用中文字型；可選底色。"""
    from docx.shared import Pt
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    if isinstance(lines, str):
        lines = [lines]
    if not lines:
        lines = ['']
    cell.text = ''
    p = cell.paragraphs[0]
    for j, ln in enumerate(lines):
        run = p.add_run(str(ln))
        run.bold = bold
        run.font.size = Pt(size)
        run.font.name = 'Arial'
        run._element.rPr.rFonts.set(qn('w:eastAsia'), cjk_font)
        if j < len(lines) - 1:
            run.add_break()
    if shade:
        tcPr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement('w:shd')
        shd.set(qn('w:val'), 'clear')
        shd.set(qn('w:fill'), shade)
        tcPr.append(shd)


def _build_shipping_docx(rows, date_label):
    """用 python-docx 產生出貨單 .docx，回傳 BytesIO。"""
    import io
    from docx import Document
    from docx.shared import Pt, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml.ns import qn

    CJK = '微軟正黑體'
    doc = Document()

    # Normal 樣式：預設字型 + 中文字型
    normal = doc.styles['Normal']
    normal.font.name = 'Arial'
    normal.font.size = Pt(10)
    normal.element.rPr.rFonts.set(qn('w:eastAsia'), CJK)

    # 橫向 A4 + 邊界（表格較寬）
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = sec.page_height, sec.page_width
    sec.left_margin = sec.right_margin = Cm(1.2)
    sec.top_margin = sec.bottom_margin = Cm(1.2)

    # 標題
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    trun = title.add_run(f"食在好蔬 — 出貨單　{date_label}")
    trun.bold = True
    trun.font.size = Pt(16)
    trun.font.name = 'Arial'
    trun._element.rPr.rFonts.set(qn('w:eastAsia'), CJK)

    headers = ['訂單編號', '姓名', '電話', '取貨地點', '品項', '份數', '金額', '付款', '備註', '狀態']
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = table.rows[0].cells
    for i, h in enumerate(headers):
        _docx_set_cell(hdr[i], h, CJK, bold=True, shade='D9D9D9', size=10)  # 表頭灰底（黑白灰，省墨）

    total_qty = 0
    total_amt = 0
    closed_count = 0
    for r in rows:
        try:
            raw = json.loads(r['order_details']) if r.get('order_details') else {}
        except Exception:
            raw = {}
        inner = raw.get('order_details') if isinstance(raw.get('order_details'), dict) else raw
        cust = raw.get('customer_info') if isinstance(raw.get('customer_info'), dict) else {}
        remarks = (cust.get('remarks') or '').strip()
        if remarks == '無':
            remarks = ''
        item_lines = (_packs_to_lines(inner.get('packs', []))
                      + _items_to_lines(inner.get('items', []))) or ['—']
        qty = int(r.get('total_quantity') or inner.get('total_quantity') or 0)
        amt = int(r.get('grand_total') or 0)
        total_qty += qty
        total_amt += amt
        # 付款欄印成兩行：方式／狀態
        pay = [
            _PAY_METHOD_LABELS.get(r.get('payment_method'), r.get('payment_method') or ''),
            _PAY_STATUS_LABELS.get(r.get('payment_status'), r.get('payment_status') or ''),
        ]
        # 已結案（已取貨）→ 整列灰底 + 狀態欄標註，避免撿貨時誤撿
        picked = bool(r.get('is_picked_up'))
        if picked:
            closed_count += 1
        row_shade = 'EDEDED' if picked else None
        status_txt = '✓ 已結案' if picked else '待出貨'
        cells = table.add_row().cells
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

    # 統計列
    doc.add_paragraph()
    summ = doc.add_paragraph()
    pending_count = len(rows) - closed_count
    summary_text = (f"共 {len(rows)} 筆（待出貨 {pending_count}"
                    + (f"　·　已結案 {closed_count}" if closed_count else "")
                    + f"）　·　總份數 {total_qty}　·　總金額 NT$ {total_amt}")
    srun = summ.add_run(summary_text)
    srun.bold = True
    srun.font.size = Pt(12)
    srun.font.name = 'Arial'
    srun._element.rPr.rFonts.set(qn('w:eastAsia'), CJK)
    if closed_count:
        note = doc.add_paragraph()
        nrun = note.add_run('※ 灰底／標「✓ 已結案」者為已取貨訂單，請勿重複出貨。')
        nrun.font.size = Pt(9)
        nrun.font.name = 'Arial'
        nrun._element.rPr.rFonts.set(qn('w:eastAsia'), CJK)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


@app.route('/api/orders/export-shipping')
@login_required
def api_export_shipping():
    """匯出當前所選出貨日的訂單成 Word 出貨單。date=YYYY-MM-DD 或 none（未指定出貨日）。"""
    date = (request.args.get('date') or '').strip()
    is_none = (date == 'none')
    if not is_none:
        try:
            datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            return jsonify({"ok": False, "error": "請先選一個出貨日期再匯出"}), 400
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        base_sql = """
            SELECT order_id, customer_name, customer_phone, grand_total,
                   order_details, payment_method, payment_status, total_quantity,
                   is_picked_up, pickup_location
            FROM `Order`
            WHERE order_status='active' AND {date_cond}
            ORDER BY pickup_location ASC, is_picked_up ASC, order_time ASC
        """
        if is_none:
            cur.execute(base_sql.format(date_cond="expected_ship_date IS NULL"))
        else:
            cur.execute(base_sql.format(date_cond="expected_ship_date = %s"), (date,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[export_shipping] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500

    if not rows:
        return jsonify({"ok": False, "error": "當日沒有要出貨的訂單"}), 404

    date_label = '未指定出貨日' if is_none else _format_ship_date_zh(date)
    try:
        buf = _build_shipping_docx(rows, date_label)
    except ImportError:
        return jsonify({"ok": False, "error": "伺服器未安裝 python-docx，請先 pip install python-docx"}), 500
    except Exception as e:
        print(f"[export_shipping build] {e}")
        return jsonify({"ok": False, "error": "產生文件失敗"}), 500

    fname = f"出貨單_{'未指定' if is_none else date.replace('-', '')}.docx"
    audit('export_shipping', date=date, count=len(rows))
    return send_file(
        buf, as_attachment=True, download_name=fname,
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )


# 註：已移除轉帳時代殘留的 /mark-paid、/cancel-paid 顧客自助回報端點。
#     現金模式（取貨付現）不需要顧客回報付款，且這兩個端點無 login_required / CSRF，
#     會讓任何人翻動訂單的 unpaid↔pending_check 狀態 → 一併清除以縮小攻擊面。


@app.route('/api/orders/<order_id>/cancel', methods=['POST'])
def api_customer_cancel(order_id):
    """顧客自行取消「店家尚未接單」的訂單。
    安全：四重條件一次擋掉越權 + 越狀態 —
      line_id=自己 AND order_status='active' AND is_confirmed=FALSE。"""
    ip = _client_ip()
    if not _check_order_rate(ip):
        return jsonify({"ok": False, "error": "請求過於頻繁"}), 429
    if not order_id.isdigit() or not (10 <= len(order_id) <= 25):
        return jsonify({"ok": False, "error": "invalid order_id"}), 400
    line_id = session.get('line_id')
    if not line_id:
        return jsonify({"ok": False, "error": "not_logged_in"}), 401
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            UPDATE `Order`
            SET order_status='cancelled', cancelled_at=NOW(), cancelled_by='customer'
            WHERE order_id=%s
              AND line_id=%s
              AND order_status='active'
              AND is_confirmed=FALSE
        """, (order_id, line_id))
        affected = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if affected == 0:
            return jsonify({"ok": False, "error": "此訂單無法取消（已付款或非本人訂單）"}), 409
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[customer_cancel] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500


@app.route('/api/orders/<order_id>/admin-cancel', methods=['POST'])
@login_required
@csrf_required
def api_admin_cancel(order_id):
    """後台取消訂單 — 限 active 且未交付。"""
    if not order_id.isdigit() or not (10 <= len(order_id) <= 25):
        return jsonify({"ok": False, "error": "invalid order_id"}), 400
    note = ((request.get_json(silent=True) or {}).get('note') or '').strip()[:200]
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            UPDATE `Order`
            SET order_status='cancelled', cancelled_at=NOW(), cancelled_by='admin', status_note=%s
            WHERE order_id=%s AND order_status='active' AND is_picked_up=FALSE
        """, (note or None, order_id))
        affected = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if affected == 0:
            return jsonify({"ok": False, "error": "此訂單無法取消（已交付或已是終態）"}), 409
        audit('admin_cancel', order_id=order_id, note=note[:80])
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[admin_cancel] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500


@app.route('/api/orders/<order_id>/refund', methods=['POST'])
@login_required
@csrf_required
def api_refund(order_id):
    """後台標記退貨 — 限 active 且已交付。"""
    if not order_id.isdigit() or not (10 <= len(order_id) <= 25):
        return jsonify({"ok": False, "error": "invalid order_id"}), 400
    note = ((request.get_json(silent=True) or {}).get('note') or '').strip()[:200]
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            UPDATE `Order`
            SET order_status='refunded', refunded_at=NOW(), status_note=%s
            WHERE order_id=%s AND order_status='active' AND is_picked_up=TRUE
        """, (note or None, order_id))
        affected = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if affected == 0:
            return jsonify({"ok": False, "error": "此訂單無法標記退貨（尚未交付或已是終態）"}), 409
        audit('refund', order_id=order_id, note=note[:80])
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[refund] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500


@app.route('/api/orders/<order_id>/confirm-order', methods=['POST'])
@login_required
@csrf_required
def api_confirm_order(order_id):
    """後台「確認訂單」（現金模式接單）：
      標記 payment_method='cash' + 寫 qr_token + 推 LINE「下單成功，帶 QR 來取貨付款」
      + 生成 QR 存本地（推圖給用戶之後做）。限 active 且 unpaid。"""
    if not order_id.isdigit() or not (10 <= len(order_id) <= 25):
        return jsonify({"ok": False, "error": "invalid order_id"}), 400
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
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
        # 推 LINE「下單成功，帶 QR 來取貨付款」（worker 端 order_confirmed template）
        # qr_file 檔名是 order_id+token 決定論產生，worker 用它組公開圖網址推圖給顧客
        # 取貨日期：後台已指定預計出貨日就一併告知顧客（DATE 欄→isoformat→中文格式）；未指定則留空
        _sd = order.get('expected_ship_date')
        ship_date_text = _format_ship_date_zh(_sd.isoformat()) if _sd else ""
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
        conn.commit()
        cur.close()
        conn.close()
        # 生成 QR 存本地（transaction 外，失敗不影響確認）
        generate_order_qr(order_id, qr_token)
        audit('confirm_order', order_id=order_id, queued=queued)
        return jsonify({"ok": True, "queued": queued})
    except Exception as e:
        print(f"[confirm_order] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500


# ==================== 店員掃碼核銷（取貨） ====================
def _parse_qr_payload(raw):
    """解析掃碼字串 'order_id|qr_token' → (order_id, token) 或 (None, None)。"""
    if not raw or '|' not in raw:
        return None, None
    oid, _, tok = raw.partition('|')
    oid = oid.strip()
    tok = tok.strip()
    if not oid.isdigit() or not (10 <= len(oid) <= 25):
        return None, None
    if not tok or len(tok) > 64:
        return None, None
    return oid, tok


@app.route(ADMIN_PATH + '/redeem')
@login_required
def redeem_page():
    """店員掃碼核銷頁（取貨時用相機掃顧客 QR）。"""
    return render_template('redeem.html', user=session.get('user'),
                           idle_timeout_min=IDLE_TIMEOUT_MIN,
                           csrf_token=_get_or_create_csrf())


@app.route('/api/redeem/lookup', methods=['POST'])
@login_required
@csrf_required
def api_redeem_lookup():
    """掃到 QR 後查訂單摘要（只讀不改）。驗 qr_token 相符才回資料。"""
    data = request.get_json(silent=True) or {}
    order_id, token = _parse_qr_payload(data.get('qr', ''))
    if not order_id:
        return jsonify({"ok": False, "error": "QR 格式不正確"}), 400
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT order_id, customer_name, customer_phone, grand_total,
                   order_details, payment_method, payment_status, is_confirmed,
                   order_status, is_picked_up, picked_up_at, qr_token,
                   expected_ship_date, pickup_location
            FROM `Order` WHERE order_id = %s
        """, (order_id,))
        o = cur.fetchone()
        cur.close(); conn.close()
        if not o or not o.get('qr_token') or not secrets.compare_digest(str(o['qr_token']), token):
            # 不存在 / token 不符 → 一律回相同訊息，不洩漏其他訂單是否存在
            return jsonify({"ok": False, "error": "查無此 QR（可能不是本店訂單）"}), 404
        try:
            raw = json.loads(o['order_details']) if o['order_details'] else {}
        except Exception:
            raw = {}
        # order_details 欄位存的是完整 payload（雙層）：{customer_info, order_details:{packs...}}
        # 相容只有內層的舊資料：找不到內層就退回頂層
        inner = raw.get('order_details') if isinstance(raw.get('order_details'), dict) else raw
        cust = raw.get('customer_info') if isinstance(raw.get('customer_info'), dict) else {}
        remarks = (cust.get('remarks') or '').strip()
        # 可核銷 = active 且尚未取貨
        redeemable = (o['order_status'] == 'active' and not o['is_picked_up'])
        return jsonify({
            "ok": True,
            "redeemable": redeemable,
            "order": {
                "order_id": o['order_id'],
                "name": o['customer_name'],
                "phone_tail": str(o['customer_phone'] or '')[-3:],
                "amount": int(o['grand_total'] or 0),
                "payment_method": o['payment_method'],
                "payment_status": o['payment_status'],
                "is_confirmed": bool(o['is_confirmed']),
                "pickup_label": pickup_label(o.get('pickup_location')),
                "order_status": o['order_status'],
                "is_picked_up": bool(o['is_picked_up']),
                "picked_up_at": o['picked_up_at'].isoformat(sep=' ') if o.get('picked_up_at') else None,
                "expected_ship_date": o['expected_ship_date'].isoformat() if o.get('expected_ship_date') else None,
                "packs": inner.get('packs', []),
                "items": inner.get('items', []),
                "total_quantity": inner.get('total_quantity', 0),
                "remarks": remarks if remarks and remarks != '無' else '',
            },
            "token": token,   # 回給前端，確認核銷時帶回（省去再掃一次）
        })
    except Exception as e:
        print(f"[redeem_lookup] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500


@app.route('/api/redeem/confirm', methods=['POST'])
@login_required
@csrf_required
def api_redeem_confirm():
    """確認核銷：標記 is_picked_up + picked_up_at。防偽（驗 token）+ 冪等（WHERE 條件 + rowcount）。"""
    data = request.get_json(silent=True) or {}
    order_id = str(data.get('order_id', '')).strip()
    token = str(data.get('qr_token', '')).strip()
    if not order_id.isdigit() or not (10 <= len(order_id) <= 25) or not token or len(token) > 64:
        return jsonify({"ok": False, "error": "參數不正確"}), 400
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        # 一步到位：只有 token 相符 + active + 尚未取貨 才會更新成功
        cur.execute("""
            UPDATE `Order`
               SET is_picked_up = TRUE, picked_up_at = NOW()
             WHERE order_id = %s AND qr_token = %s
               AND order_status = 'active' AND is_picked_up = FALSE
        """, (order_id, token))
        updated = cur.rowcount
        conn.commit()
        if updated == 1:
            cur.close(); conn.close()
            audit('redeem_order', order_id=order_id)
            return jsonify({"ok": True})
        # updated == 0 → 再查原因，給店員明確訊息
        cur.execute("""
            SELECT qr_token, order_status, is_picked_up, picked_up_at
            FROM `Order` WHERE order_id = %s
        """, (order_id,))
        o = cur.fetchone()
        cur.close(); conn.close()
        if not o or not o.get('qr_token') or not secrets.compare_digest(str(o['qr_token']), token):
            return jsonify({"ok": False, "error": "查無此 QR（可能不是本店訂單）"}), 404
        if o['order_status'] != 'active':
            label = '已取消' if o['order_status'] == 'cancelled' else ('已退貨' if o['order_status'] == 'refunded' else o['order_status'])
            return jsonify({"ok": False, "error": f"此訂單{label}，無法核銷", "reason": "not_active"}), 409
        if o['is_picked_up']:
            at = o['picked_up_at'].isoformat(sep=' ') if o.get('picked_up_at') else ''
            return jsonify({"ok": False, "error": f"此訂單已取貨{('（' + at + '）') if at else ''}", "reason": "already_picked"}), 409
        return jsonify({"ok": False, "error": "核銷失敗，請重試"}), 409
    except Exception as e:
        print(f"[redeem_confirm] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500


# ==================== LINE LIFF / Customer Identity APIs ====================
@app.route('/api/line/verify', methods=['POST'])
def api_line_verify():
    """驗證 LIFF 給的 ID Token，把身分寫進 session。
    無 CSRF：ID Token 本身就是密碼學驗證的憑證。"""
    ip = _client_ip()
    if not _check_line_verify_rate(ip):
        return jsonify({"error": "請求過於頻繁"}), 429

    if not LINE_LOGIN_CHANNEL_ID:
        # 沒設環境變數 → LINE 功能未啟用
        return jsonify({"error": "LINE login not configured"}), 503

    data = request.get_json(silent=True) or {}
    id_token = (data.get('idToken') or '').strip()
    if not id_token:
        return jsonify({"error": "缺少 idToken"}), 400

    claims = _verify_line_id_token(id_token)
    if not claims:
        return jsonify({"error": "ID Token 驗證失敗"}), 401

    line_id = claims['sub']
    display_name = claims.get('name', '') or ''
    picture_url = claims.get('picture', '') or ''

    # 查 CustomerLineBindings：抓已綁的姓名 / 電話（可能 None）
    bound_name = None
    bound_phone = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT Customer_Name, Phone_Number FROM CustomerLineBindings WHERE LINE_ID = %s",
            (line_id,)
        )
        row = cur.fetchone()
        if row:
            bound_name = row[0]
            bound_phone = row[1]
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[line/verify] DB lookup error: {e}")
        # DB 出錯不影響登入 — 後續下單時還會再做 binding

    # 寫 session
    session['line_id'] = line_id
    session['line_display_name'] = display_name
    session['line_picture'] = picture_url
    session['line_name'] = bound_name           # 已綁過的姓名（None 表未綁）
    session['line_phone'] = bound_phone         # 已綁過的電話（None 表未綁）

    return jsonify({
        "line_id": line_id,
        "display_name": display_name,
        "picture_url": picture_url,
        "bound_name": bound_name,
        "bound_phone": bound_phone,
    })


@app.route('/api/line/logout', methods=['POST'])
def api_line_logout():
    """清掉 session 裡所有 line_* 欄位。前端再 call liff.logout()。"""
    for k in ('line_id', 'line_display_name', 'line_picture',
              'line_name', 'line_phone'):
        session.pop(k, None)
    return jsonify({"ok": True})


@app.route('/api/orders/mine')
def api_orders_mine():
    """登入 LINE 的用戶查自己的訂單（含進行中 + 最近 60 天已完成）。
    前端再依 is_confirmed / payment_status / is_picked_up 分類。
    必須 session.line_id；否則 401。"""
    line_id = session.get('line_id')
    if not line_id:
        return jsonify({"error": "not_logged_in"}), 401
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT order_id, customer_name, customer_phone, grand_total,
                   payment_method, payment_status, is_confirmed,
                   is_picked_up, is_notified,
                   order_status, cancelled_at, refunded_at,
                   expected_ship_date, order_time, pickup_location
            FROM `Order`
            WHERE line_id = %s
              AND (
                is_picked_up = FALSE
                OR order_time >= DATE_SUB(NOW(), INTERVAL 60 DAY)
              )
            ORDER BY order_time DESC
            LIMIT 50
        """, (line_id,))
        rows = cur.fetchall()
        # 序列化 datetime / date
        for r in rows:
            for k, v in list(r.items()):
                if isinstance(v, (datetime, date)):
                    r[k] = v.isoformat()
            # MySQL bool → Python bool
            r['is_picked_up'] = bool(r.get('is_picked_up'))
            r['is_notified'] = bool(r.get('is_notified'))
            r['is_confirmed'] = bool(r.get('is_confirmed'))
        cur.close()
        conn.close()
        return jsonify({"orders": rows})
    except Exception as e:
        print(f"[orders/mine] {e}")
        return jsonify({"error": "server_error"}), 500


# 註：已移除孤兒端點 /api/orders/<id>/confirm-payment（舊「✓收款」按鈕，前端已無呼叫）。
#     收款狀態改由「收款下拉」(/payment) 純標記，不再推 LINE 通知。


@app.route('/api/orders/<order_id>/ship-date', methods=['POST'])
@login_required
@csrf_required
def api_update_ship_date(order_id):
    """後台設定/修改訂單的預計出貨日"""
    data = request.get_json() or {}
    ship_date = data.get('ship_date')  # YYYY-MM-DD 或 null
    try:
        conn = get_conn()
        cur = conn.cursor()
        if ship_date:
            cur.execute(
                "UPDATE `Order` SET expected_ship_date=%s WHERE order_id=%s",
                (ship_date, order_id)
            )
        else:
            cur.execute(
                "UPDATE `Order` SET expected_ship_date=NULL WHERE order_id=%s",
                (order_id,)
            )
        conn.commit()
        cur.close()
        conn.close()
        audit('update_ship_date', order_id=order_id, ship_date=ship_date or 'NULL')
        return jsonify({"ok": True})
    except Exception as e:
        print(f"[update_ship_date] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500


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


def _build_arrived_payload(order_id, loc):
    """到貨通知的 LINE payload。訂單編號一定要帶——
    同一支電話可能同時有兩張不同廠的訂單，沒有編號顧客無法分辨該去哪一廠。"""
    return {
        "order_id": str(order_id),
        "pickup_label": pickup_label(loc),
    }


@app.route('/api/orders/notify', methods=['POST'])
@login_required
@csrf_required
def api_notify():
    data = request.get_json() or {}
    order_ids = data.get('order_ids', [])
    if not order_ids:
        return jsonify({"ok": False, "error": "未選擇訂單"}), 400
    success = 0
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
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
                    cur.execute(
                        "UPDATE `Order` SET is_notified=TRUE WHERE order_id=%s",
                        (oid,)
                    )
                    success += 1
        conn.commit()
        cur.close()
        conn.close()
        audit('notify_arrival', order_ids=','.join(map(str, order_ids))[:200], count=success)
        return jsonify({"ok": True, "count": success})
    except Exception as e:
        print(f"[notify] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500


@app.route('/api/orders/pickup', methods=['POST'])
@login_required
@csrf_required
def api_pickup():
    data = request.get_json() or {}
    order_ids = data.get('order_ids', [])
    if not order_ids:
        return jsonify({"ok": False, "error": "未選擇訂單"}), 400
    try:
        conn = get_conn()
        cur = conn.cursor()
        placeholders = ','.join(['%s'] * len(order_ids))
        cur.execute(
            f"UPDATE `Order` SET is_picked_up=TRUE WHERE order_id IN ({placeholders})",
            tuple(order_ids)
        )
        affected = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        audit('pickup', order_ids=','.join(map(str, order_ids))[:200], count=affected)
        return jsonify({"ok": True, "count": affected})
    except Exception as e:
        print(f"[pickup] {e}")
        return jsonify({"ok": False, "error": "server_error"}), 500


@app.route('/api/customers')
@login_required
def api_customers():
    try:
        conn = get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT LINE_ID, Customer_Name, Phone_Number, Register_Time
            FROM CustomerLineBindings
            ORDER BY Customer_Name
        """)
        rows = cur.fetchall()
        for r in rows:
            r['Register_Time'] = r['Register_Time'].isoformat(sep=' ') if r['Register_Time'] else None
        cur.close()
        conn.close()
        return jsonify(rows)
    except Exception as e:
        print(f"[customers] {e}")
        return jsonify({"error": "server_error"}), 500


# ==================== 圖庫瘦身與英文化 ====================
THUMB_PRODUCTS = {
    # slug : 原始檔名清單（會壓成 slug-1.jpg, slug-2.jpg ...）
    'red-butter':    ['紅奶油 (1).JPG', '紅奶油 (2).JPG', '紅奶油 (3).JPG', '紅奶油 (4).JPG'],
    'green-butter':  ['綠奶油 (1).JPG', '綠奶油 (2).JPG', '綠奶油 (3).JPG'],
    'red-oak':       ['紅橡木 (1).JPG', '紅橡木 (2).JPG', '紅橡木 (3).JPG', '紅橡木 (4).JPG'],
    'green-oak':     ['綠橡木 (1).JPG', '綠橡木 (2).JPG', '綠橡木 (3).JPG'],
    'red-flame':     ['紅火焰 (1).JPG', '紅火焰 (2).JPG', '紅火焰 (3).JPG'],
    'green-flame':   ['綠火焰 (1).JPG', '綠火焰 (2).JPG', '綠火焰 (3).JPG'],
    'romaine':       ['蘿蔓 (1).JPG', '蘿蔓 (2).JPG', '蘿蔓 (3).JPG', '蘿蔓 (4).JPG'],
    'sweet-romaine': ['甜蘿蔓 (1).JPG', '甜蘿蔓 (2).JPG', '甜蘿蔓 (3).JPG'],
    'frilly':        ['皺葉萵苣 (1).JPG', '皺葉萵苣 (2).JPG', '皺葉萵苣 (3).JPG'],
    'sweet-crisp':   ['甜脆萵苣 (1).JPG', '甜脆萵苣 (2).JPG', '甜脆萵苣 (3).JPG', '甜脆萵苣 (4).JPG'],
    'kale':          ['羽衣甘藍 (1).JPG', '羽衣甘藍 (2).JPG', '羽衣甘藍 (3).JPG'],
    'arugula':       ['芝麻頁 (1).JPG', '芝麻頁 (2).JPG', '芝麻頁 (3).JPG'],
}

THUMB_SITES = [
    '場所 (1).JPG', '場所 (2).JPG', '場所 (3).JPG', '場所 (4).JPG',
    '場所 (5).jpg', '場所 (6).JPG', '場所 (7).JPG',
]


def generate_thumbnails(src_dir='菜場域圖庫', out_root='img'):
    """啟動時自動把大照片壓到 web 可用大小，存入 img/menu 與 img/site。"""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        print("[thumbs] 未安裝 Pillow，跳過圖片瘦身。請執行 pip install Pillow")
        return

    # 用 app 所在目錄當基準，與 send_from_directory 一致
    base = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.join(base, src_dir)
    out_root = os.path.join(base, out_root)
    menu_dir = os.path.join(out_root, 'menu')
    site_dir = os.path.join(out_root, 'site')
    os.makedirs(menu_dir, exist_ok=True)
    os.makedirs(site_dir, exist_ok=True)

    def resize_save(src_path, dst_path, max_size=900):
        if not os.path.exists(src_path):
            return False
        # 快取：若已存在且 newer than source，跳過
        if os.path.exists(dst_path):
            try:
                if os.path.getmtime(dst_path) >= os.path.getmtime(src_path):
                    return True
            except Exception:
                pass
        try:
            with Image.open(src_path) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode in ('RGBA', 'LA', 'P'):
                    im = im.convert('RGB')
                im.thumbnail((max_size, max_size), Image.LANCZOS)
                im.save(dst_path, 'JPEG', quality=82, optimize=True, progressive=True)
            return True
        except Exception as e:
            print(f"[thumbs]   ! {os.path.basename(src_path)}: {e}")
            return False

    print("[thumbs] 產生商品照（max 900px）...")
    n_ok = 0
    for slug, sources in THUMB_PRODUCTS.items():
        for i, name in enumerate(sources, 1):
            src = os.path.join(src_dir, name)
            dst = os.path.join(menu_dir, f'{slug}-{i}.jpg')
            if resize_save(src, dst, max_size=900):
                n_ok += 1

    print("[thumbs] 產生場域照（max 1600px）...")
    n_site = 0
    for i, name in enumerate(THUMB_SITES, 1):
        src = os.path.join(src_dir, name)
        dst = os.path.join(site_dir, f'site-{i}.jpg')
        if resize_save(src, dst, max_size=1600):
            n_site += 1

    print(f"[thumbs] 完成 — 商品照 {n_ok} 張 / 場域照 {n_site} 張")


def _print_security_warnings():
    warns = []
    if app.config['SECRET_KEY'] == _DEFAULT_SECRET:
        warns.append("FLASK_SECRET 未設定（使用預設值）— 正式部署請設環境變數")
    if not app.config.get('SESSION_COOKIE_SECURE'):
        warns.append("HTTPS 部署請設 FLASK_SECURE_COOKIE=1，否則 cookie 可能被竊聽")
    if not ADMIN_USER or not ADMIN_PASS:
        warns.append("ADMIN_USER / ADMIN_PASS 未設定 — 後台登入目前停用，請在 .env 設定")
    if not ADMIN_TOTP_SECRET:
        warns.append("後台 2FA 未啟用 — 建議執行 python setup_2fa.py 後把 ADMIN_TOTP_SECRET 設進 .env")
    if not DB_CONFIG['password'] or not DB_CONFIG['user']:
        warns.append("DB 帳密未設定 — 請在 .env 設 DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME")
    if warns:
        print("\n  [警告] 安全提醒：")
        for w in warns:
            print(f"   - {w}")


if __name__ == '__main__':
    init_db()
    generate_thumbnails()
    print(f"\n  食在好蔬 Web 已啟動")
    print(f"  首頁：  http://localhost:5000/")
    print(f"  訂購：  http://localhost:5000/pos")
    # 不在啟動訊息印密碼，避免 log 外洩
    print(f"  後台：  http://localhost:5000{ADMIN_PATH}")
    _print_security_warnings()
    # 預設 debug=False 才安全；要看詳細錯誤訊息開發時自行改 True
    debug_mode = os.environ.get('FLASK_DEBUG') == '1'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
