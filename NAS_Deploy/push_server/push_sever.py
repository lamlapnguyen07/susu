from flask import Flask, request, abort, send_from_directory
import re
import hmac
import hashlib
import base64
from linebot import LineBotApi, WebhookHandler

from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage
import json
import os
import threading
import time
from datetime import datetime
import mysql.connector
from mysql.connector import Error

app = Flask(__name__)

# 自動讀取同目錄的 .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# LINE Bot 設定（從環境變數讀取，請填入 .env）
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', ''))
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET', '')
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 資料庫設定
# DB 設定：與 app.py 共用同一組環境變數（.env）。不放寫死帳密，避免原始碼外洩。
DB_CONFIG = {
    'host':     os.environ.get('DB_HOST', '127.0.0.1'),
    'port':     int(os.environ.get('DB_PORT', '3306')),
    'user':     os.environ.get('DB_USER', ''),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', ''),
    'charset':  'utf8mb4'
}

# 公開網域（給顧客的 QR 圖網址前綴）。
# 對外網址（顧客的 QR 圖網址 + LINE webhook）。固定網域版：
# 請在 .env 設 PUBLIC_BASE_URL=https://你的worker對外網域（Cloudflare Tunnel → 本機 :4999）。
PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')

# QR 成品圖資料夾（與 app.py 共用同一個「訂單」資料夾 — 兩者在同一台機器）
ORDER_QR_DIR = os.environ.get('ORDER_QR_DIR') or \
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '訂單')
# 公開圖檔名格式：<order_id>_<qr_token>.png（不可猜；擋路徑穿越/列舉）
ORDER_QR_FILENAME_RE = re.compile(r'^[0-9]{6,50}_[A-Za-z0-9_-]{6,64}\.png$')

# 用戶狀態追蹤 (記錄用戶目前的操作狀態)
user_states = {}

# Webhook 事件去重
processed_webhook_events = set()

def is_duplicate_webhook_event(event_data):
    """檢查是否為重複的 webhook 事件"""
    webhook_event_id = event_data.get('webhookEventId')
    if not webhook_event_id:
        return False
    
    if webhook_event_id in processed_webhook_events:
        return True
    
    processed_webhook_events.add(webhook_event_id)
    # 限制記錄數量，避免記憶體無限增長
    if len(processed_webhook_events) > 10000:
        # 清除一半的舊記錄
        old_events = list(processed_webhook_events)[:5000]
        for old_event in old_events:
            processed_webhook_events.remove(old_event)
    
    return False

def get_database_connection():
    """
    取得 MySQL 資料庫連線
    回傳:
        connection 物件 或 None (若連線失敗)
    """
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        return connection
    except Error as e:
        print(f"❌ [DB] 資料庫連線錯誤: {e}")
        return None

def register_customer(user_id, name, phone):
    """
    將用戶的 LINE ID 與姓名、電話綁定寫入資料庫
    參數:
        user_id: LINE 官方提供的唯一識別碼
        name: 客人姓名
        phone: 客人手機號碼
    回傳:
        (bool 成功與否, str 系統回覆訊息)
    """
    try:
        print(f"📝 [註冊] 正在為 {name} ({phone}) 建立綁定資料...")
        connection = get_database_connection()
        if not connection:
            return False, "資料庫連線失敗，請稍後再試。"
        
        cursor = connection.cursor()
        
        # 確保資料表存在
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS CustomerLineBindings (
                LINE_ID VARCHAR(255) PRIMARY KEY,
                Customer_Name VARCHAR(100) NOT NULL,
                Phone_Number VARCHAR(20) NOT NULL UNIQUE,
                Register_Time DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 檢查電話是否已經被其他 LINE 帳號綁定過 (防止盜用或重複綁定)
        cursor.execute("SELECT LINE_ID FROM CustomerLineBindings WHERE Phone_Number = %s", (phone,))
        existing = cursor.fetchone()
        if existing and existing[0] != user_id:
            print(f"⚠️ [註冊] 失敗: 電話 {phone} 已被其他帳號綁定。")
            return False, f"❌ 此手機號碼 {phone} 已被其他 LINE 帳號註冊！若有疑問請聯絡客服。"
            
        # 寫入或更新綁定資料 (若已存在則覆蓋更新姓名與電話)
        cursor.execute("""
            INSERT INTO CustomerLineBindings (LINE_ID, Customer_Name, Phone_Number, Register_Time)
            VALUES (%s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE 
                Customer_Name = VALUES(Customer_Name),
                Phone_Number = VALUES(Phone_Number),
                Register_Time = NOW()
        """, (user_id, name, phone))
        
        # 1. 回溯更新該手機號碼對應的歷史訂單，補上 line_id
        try:
            cursor.execute("""
                UPDATE `Order` 
                SET line_id = %s 
                WHERE customer_phone = %s AND (line_id IS NULL OR line_id = '')
            """, (user_id, phone))
            print(f"🔄 [註冊] 已自動綁定 {cursor.rowcount} 筆歷史訂單。")
        except Exception as ex:
            print(f"⚠️ [註冊] 綁定歷史訂單時發生錯誤: {ex}")
            
        # 2. 回溯更新該手機號碼對應且之前因未註冊而 failed 的通知，狀態改回 pending
        try:
            cursor.execute("""
                UPDATE LineNotificationQueue 
                SET status = 'pending' 
                WHERE phone = %s AND status = 'failed'
            """, (phone,))
            if cursor.rowcount > 0:
                print(f"🔄 [註冊] 已將 {cursor.rowcount} 筆失敗的通知重新排入佇列。")
        except Exception as ex:
            print(f"⚠️ [註冊] 重新排入通知佇列時發生錯誤: {ex}")
        
        connection.commit()
        cursor.close()
        connection.close()
        
        print(f"✅ [註冊] 成功: {name} ({phone}) 已綁定 LINE_ID: {user_id[:8]}...")
        return True, f"✅ 註冊/修改成功！\n姓名：{name}\n手機：{phone}\n未來有您的訂單或到貨時，系統會自動通知您。"
        
    except Error as e:
        # 只記在伺服器 log，不把 DB 內部錯誤細節回傳給用戶（避免資訊外洩）
        print(f"❌ [註冊] 資料庫操作發生錯誤: {e}")
        return False, "❌ 註冊失敗，系統忙碌中，請稍後再試一次。"

def check_user_registered(user_id):
    """
    檢查用戶是否已經註冊過
    回傳:
        (bool 是否已註冊, str 姓名, str 電話)
    """
    try:
        connection = get_database_connection()
        if not connection:
            return False, None, None
            
        cursor = connection.cursor(dictionary=True)
        # 確保資料表存在，避免第一次執行就報錯
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS CustomerLineBindings (
                LINE_ID VARCHAR(255) PRIMARY KEY,
                Customer_Name VARCHAR(100) NOT NULL,
                Phone_Number VARCHAR(20) NOT NULL UNIQUE,
                Register_Time DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("SELECT Customer_Name, Phone_Number FROM CustomerLineBindings WHERE LINE_ID = %s", (user_id,))
        user = cursor.fetchone()
        cursor.close()
        connection.close()
        
        if user:
            return True, user['Customer_Name'], user['Phone_Number']
        return False, None, None
        
    except Error as e:
        print(f"❌ [DB] 檢查用戶註冊狀態發生錯誤: {e}")
        return False, None, None

def send_line_message(user_id, message):
    """
    發送主動推播訊息 (Push Message) 給指定的 LINE 用戶
    """
    try:
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text=message)
        )
        print(f"📤 [推播] 成功發送訊息給用戶 {user_id[:8]}... | 內容截取: {message[:15]}...")
        return True
    except Exception as e:
        print(f"❌ [推播] 發送失敗，目標用戶 {user_id[:8]}... | 錯誤原因: {e}")
        return False

def send_text_and_image(user_id, text, image_url):
    """推【文字 + QR 圖片】(單則通知)。圖片失敗時降級為只推文字，確保文字一定送達。"""
    try:
        line_bot_api.push_message(user_id, [
            TextSendMessage(text=text),
            ImageSendMessage(original_content_url=image_url, preview_image_url=image_url),
        ])
        print(f"📤 [推播] 文字+QR圖 成功發送給 {user_id[:8]}...")
        return True
    except Exception as e:
        print(f"⚠️ [推播] 圖片推送失敗，降級為只推文字: {e}")
        return send_line_message(user_id, text)

def init_notification_queue():
    """建立通知佇列資料表（若不存在）"""
    try:
        connection = get_database_connection()
        if not connection:
            return
        cursor = connection.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS LineNotificationQueue (
                id INT AUTO_INCREMENT PRIMARY KEY,
                type VARCHAR(20) NOT NULL,
                phone VARCHAR(20) NOT NULL,
                payload JSON,
                status VARCHAR(20) DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.commit()
        cursor.close()
        connection.close()
        print("✅ [佇列] 通知佇列資料表確認完成。")
    except Exception as e:
        print(f"❌ [佇列] 建立資料表失敗: {e}")

# ================= 新增的三大查詢功能 =================

def query_active_orders(user_id):
    """
    查詢即時訂單狀態 (未取貨/進行中)
    """
    try:
        connection = get_database_connection()
        if not connection:
            return "❌ 系統暫時無法查詢，請稍後再試。"

        cursor = connection.cursor(dictionary=True)
        # 篩選條件：is_picked_up = False (或 0)
        cursor.execute("""
            SELECT order_id, order_time, grand_total,
                   payment_method, is_notified, is_picked_up
            FROM `Order`
            WHERE line_id = %s AND is_picked_up = False
            ORDER BY order_time DESC
        """, (user_id,))
        active_orders = cursor.fetchall()
        cursor.close()
        connection.close()

        if not active_orders:
            return "✅ 您目前沒有進行中的訂單喔！\n歡迎點選選單選購最新鮮的蔬菜。"

        payment_map = {'unpaid': '💳 未收款', 'cash': '💵 現金', 'transfer': '🏦 轉帳'}
        lines = ["🔄 【進行中的訂單】\n"]
        
        for o in active_orders:
            oid = str(o['order_id'])
            t   = str(o['order_time'])[:16]
            amt = o['grand_total']
            pay = payment_map.get(o['payment_method'], '未知')
            notif = '✅ 已通知到貨 (請盡速取件)' if o['is_notified'] else '⏳ 處理中 (尚未到貨)'
            
            lines.append(
                f"─────────────\n"
                f"訂單編號：{oid}\n"
                f"下單時間：{t}\n"
                f"金額：NT$ {amt}\n"
                f"狀態：{notif}"
            )
        return "\n".join(lines)

    except Exception as e:
        print(f"❌ [查詢進行中訂單] 錯誤: {e}")
        return "❌ 查詢訂單時發生錯誤，請稍後再試。"

def query_historical_orders(user_id):
    """
    查詢歷史訂單 (已取貨/已結案)，限制最近 5 筆
    """
    try:
        connection = get_database_connection()
        if not connection:
            return "❌ 系統暫時無法查詢，請稍後再試。"

        cursor = connection.cursor(dictionary=True)
        # 篩選條件：is_picked_up = True (或 1)
        cursor.execute("""
            SELECT order_id, order_time, grand_total, payment_method
            FROM `Order`
            WHERE line_id = %s AND is_picked_up = True
            ORDER BY order_time DESC
            LIMIT 5
        """, (user_id,))
        done_orders = cursor.fetchall()
        cursor.close()
        connection.close()

        if not done_orders:
            return "📋 您目前還沒有歷史完成的訂單紀錄喔！"

        lines = ["📦 【歷史訂單紀錄 (最近5筆)】\n"]
        for o in done_orders:
            oid = str(o['order_id'])
            t   = str(o['order_time'])[:10] # 只取日期
            amt = o['grand_total']
            lines.append(f"• {t} | NT$ {amt} (單號: {oid})")
            
        return "\n".join(lines)

    except Exception as e:
        print(f"❌ [查詢歷史訂單] 錯誤: {e}")
        return "❌ 查詢紀錄時發生錯誤，請稍後再試。"

# (保留原本整合查詢函數，以防有其他地方呼叫)
def query_user_orders(user_id):
    """
    依 LINE UID 查詢該用戶的訂單記錄
    回傳格式化的訂單狀態字串
    """
    try:
        connection = get_database_connection()
        if not connection:
            return "❌ 系統暫時無法查詢，請稍後再試。"

        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT order_id, order_time, grand_total,
                   payment_method, is_notified, is_picked_up
            FROM `Order`
            WHERE line_id = %s
            ORDER BY order_time DESC
            LIMIT 10
        """, (user_id,))
        orders = cursor.fetchall()
        cursor.close()
        connection.close()

        if not orders:
            return ("📋 目前查無您的訂單記錄。\n\n"
                    "若您已在店內下單，請確認已完成 LINE 綁定，"
                    "或洽詢店員協助確認。")

        payment_map = {'unpaid': '💳 未收款', 'cash': '💵 現金', 'transfer': '🏦 轉帳'}

        lines = ["📦 【您的訂單查詢結果】\n"]
        active, done = [], []
        for o in orders:
            if o['is_picked_up']:
                done.append(o)
            else:
                active.append(o)

        if active:
            lines.append("🔄 進行中的訂單：")
            for o in active:
                oid = str(o['order_id'])
                t   = str(o['order_time'])[:16]
                amt = o['grand_total']
                pay = payment_map.get(o['payment_method'], '未知')
                notif = '✅ 已通知到貨' if o['is_notified'] else '⏳ 尚未到貨'
                lines.append(
                    f"─────────────\n"
                    f"訂單編號：{oid}\n"
                    f"下單時間：{t}\n"
                    f"金額：NT$ {amt}\n"
                    f"收款狀態：{pay}\n"
                    f"到貨狀態：{notif}"
                )

        if done:
            lines.append("\n✅ 已結案訂單（最近 5 筆）：")
            for o in done[:5]:
                oid = str(o['order_id'])
                t   = str(o['order_time'])[:16]
                amt = o['grand_total']
                lines.append(f"• {t}  NT$ {amt}  (#{oid})")

        return "\n".join(lines)

    except Exception as e:
        print(f"❌ [查詢訂單] 錯誤: {e}")
        return "❌ 查詢訂單時發生錯誤，請稍後再試。"
# ===================================================

def process_notification_queue():
    """背景執行緒：每 5 秒輪詢佇列，發送 LINE 通知"""
    print("🔄 [佇列] 通知佇列監控執行緒已啟動，每 5 秒輪詢一次。")
    while True:
        connection = None
        try:
            connection = get_database_connection()
            if connection:
                cursor = connection.cursor(dictionary=True)
                cursor.execute("""
                    SELECT * FROM LineNotificationQueue
                    WHERE status = 'pending'
                    ORDER BY created_at ASC LIMIT 10
                """)
                rows = cursor.fetchall()
                for row in rows:
                    try:
                        phone = row['phone']
                        payload = json.loads(row['payload']) if row['payload'] else {}
                        user_cursor = connection.cursor(dictionary=True)
                        user_cursor.execute(
                            "SELECT LINE_ID, Customer_Name FROM CustomerLineBindings WHERE Phone_Number = %s",
                            (phone,)
                        )
                        user = user_cursor.fetchone()
                        user_cursor.close()
                        if user:
                            line_id = user['LINE_ID']
                            name = user['Customer_Name']
                            qr_image_url = None   # order_confirmed 才會設值
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
                                # 組 QR 圖公開網址（需設 PUBLIC_BASE_URL；缺檔名或網域則只推文字）
                                qr_file = payload.get('qr_file', '')
                                if PUBLIC_BASE_URL and qr_file:
                                    qr_image_url = f"{PUBLIC_BASE_URL}/order-qr/{qr_file}"
                            else:
                                cursor.execute("UPDATE LineNotificationQueue SET status='failed' WHERE id=%s", (row['id'],))
                                continue
                            if qr_image_url:
                                send_text_and_image(line_id, msg, qr_image_url)
                            else:
                                send_line_message(line_id, msg)
                            cursor.execute("UPDATE LineNotificationQueue SET status='done' WHERE id=%s", (row['id'],))
                            print(f"✅ [佇列] ID {row['id']} ({row['type']}) 已發送給 {phone}。")
                        else:
                            cursor.execute("UPDATE LineNotificationQueue SET status='failed' WHERE id=%s", (row['id'],))
                            print(f"⚠️ [佇列] 找不到電話 {phone} 的 LINE 綁定，標記 failed。")
                    except Exception as e:
                        print(f"❌ [佇列] 處理 ID {row['id']} 失敗: {e}")
                        try:
                            cursor.execute("UPDATE LineNotificationQueue SET status='failed' WHERE id=%s", (row['id'],))
                        except:
                            pass
                connection.commit()
        except Exception as e:
            print(f"❌ [佇列] 輪詢例外: {e}")
        finally:
            if connection and connection.is_connected():
                try:
                    if 'cursor' in locals() and cursor:
                        cursor.close()
                except Exception:
                    pass
                connection.close()
        time.sleep(5)

@app.route("/")
def hello():
    return "LINE Bot is running!"

@app.route('/order-qr/<filename>')
def serve_order_qr(filename):
    """透過 worker 的公開網域服務取貨 QR 圖（供 LINE 伺服器抓圖）。
    無需登入：保護來自不可猜檔名 <order_id>_<token>.png。嚴格驗證擋穿越/列舉。"""
    if not ORDER_QR_FILENAME_RE.match(filename or ''):
        abort(404)
    resp = send_from_directory(ORDER_QR_DIR, filename)
    resp.headers['Cache-Control'] = 'private, max-age=86400'
    return resp

@app.route("/botgoodveg/callback", methods=['POST'])
def callback():
    """
    接收 LINE 官方傳送的 Webhook 事件
    """
    # 取得 request body (純文本)
    body = request.get_data(as_text=True)

    # === 驗證 X-Line-Signature：確認請求真的來自 LINE，擋掉偽造的 webhook ===
    # LINE 規格：signature = Base64( HMAC-SHA256(channel_secret, request_body) )
    if not LINE_CHANNEL_SECRET:
        print("🚫 [Webhook] 未設定 LINE_CHANNEL_SECRET，無法驗簽章 → 拒絕請求")
        abort(400)
    signature = request.headers.get('X-Line-Signature', '')
    expected = base64.b64encode(
        hmac.new(LINE_CHANNEL_SECRET.encode('utf-8'), body.encode('utf-8'), hashlib.sha256).digest()
    ).decode('utf-8')
    if not signature or not hmac.compare_digest(signature, expected):
        print("🚫 [Webhook] X-Line-Signature 驗證失敗 → 拒絕（可能是偽造請求）")
        abort(400)

    try:
        events = json.loads(body).get('events', [])
        
        for event_data in events:
            # 檢查並過濾重複的 webhook 事件
            if is_duplicate_webhook_event(event_data):
                print(f"🔄 [Webhook] 忽略重複事件 ID: {event_data.get('webhookEventId')}")
                continue
            
            # 只處理文字訊息事件
            if event_data['type'] == 'message' and event_data['message']['type'] == 'text':
                user_id = event_data['source']['userId']
                user_message = event_data['message']['text'].strip()
                reply_token = event_data['replyToken']
                
                print(f"💬 [訊息接收] 用戶: {user_id[:8]}... | 說了: {user_message}")
                
                # 將訊息交給核心邏輯處理，取得要回覆的文字
                reply_message = process_user_message(user_id, user_message)
                
                # 機器人保持靜默時不發送任何回覆
                if reply_message is not None:
                    line_bot_api.reply_message(
                        reply_token,
                        TextSendMessage(text=reply_message)
                    )
                    print(f"📩 [訊息回覆] 成功回應給用戶: {reply_message[:20]}...")
                else:
                    print(f"🔇 [訊息回覆] 指令不認識，機器人保持靜默: {user_message}")
        
        return 'OK'
        
    except Exception as e:
        print(f"❌ [Webhook] 處理訊息時發生錯誤: {e}")
        import traceback
        traceback.print_exc()
        return 'OK'  # 回傳 200 避免 LINE 認為伺服器掛掉而不斷重試

def process_user_message(user_id, user_message):
    """處理用戶訊息的邏輯 (已優化資料修改與防呆機制)"""
    
    user_message = user_message.strip()
    
    # === 1. 優先攔截「取消」指令 ===
    if user_message.lower() in ["取消", "cancel"]:
        if user_id in user_states:
            user_states.pop(user_id, None)
            return "✅ 已取消目前操作。"
        return "目前沒有進行中的操作喔！"

    # 定義所有主選單會發送的指令 (客人只要按了選單，就解除他之前卡住的等待狀態)
    menu_commands = ["會員資料", "登記", "會員登記", "註冊", "修改資料", 
                     "即時訂單", "訂單狀態", "進行中訂單", "歷史訂單", "歷史紀錄", 
                     "查詢訂單", "訂單查詢", "我的訂單"]

    if user_message in menu_commands:
        user_states.pop(user_id, None)

    # === 2. 處理「會員資料 / 登記」流程 (展示資料並立刻進入等待輸入狀態) ===
    if user_message in ["會員資料", "登記", "會員登記", "註冊", "修改資料"]:
        is_registered, current_name, current_phone = check_user_registered(user_id)
        
        # 🌟 關鍵：不管他是要看資料還是要登記，系統都立刻將他設為「準備接收新姓名與電話」的狀態
        user_states[user_id] = "waiting_for_veg_registration"
        
        if is_registered:
            # 客人看到這則訊息時，直接打字就能更新，不需要再做任何宣告
            return (f"👤 【您的會員資料】\n"
                    f"姓名：{current_name}\n"
                    f"電話：{current_phone}\n\n"
                    f"✏️ 若需更新資料，請【直接回傳】新姓名與新手機號碼，中間請空一格。\n"
                    f"範例：王大明 0987654321\n\n"
                    f"👉 (若不需修改，請直接忽略此訊息，或點擊選單其他功能)")
        else:
            return ("⚠️ 您尚未註冊會員。\n\n"
                    "請輸入您的「姓名」與「手機號碼」，中間以空格分隔。\n"
                    "範例：王小明 0912345678\n\n"
                    "(若想中斷註冊，請隨時輸入「取消」)")

    # === 3. 處理「等待輸入狀態」下的用戶對話 (直接接收並更新) ===
    elif user_states.get(user_id) == "waiting_for_veg_registration":
        parts = user_message.split()
        if len(parts) >= 2:
            # 防呆1：如果姓名中間有空白，取最後一段當作電話，前面全部合併為姓名
            raw_phone = parts[-1]
            name = " ".join(parts[:-1])
            
            # 防呆2：過濾掉電話中的非數字字元 (容許 0912-345-678 這種打法)
            phone = ''.join(filter(str.isdigit, raw_phone))
            
            # 防呆3：驗證電話格式是否為 10 碼且 09 開頭
            if not phone.startswith('09') or len(phone) != 10:
                return f"⚠️ 手機號碼格式似乎不對喔！\n您輸入的電話是：{raw_phone}\n請確認必須是 09 開頭的 10 碼數字。\n\n請重新輸入一次您的「姓名」與「手機號碼」："
            
            # 直接寫入資料庫覆蓋舊資料
            success, message = register_customer(user_id, name, phone)
            
            # 防呆4：更新成功才清除狀態
            if success:
                user_states.pop(user_id, None)
            return message
        else:
            return "⚠️ 格式似乎漏了什麼喔！\n請輸入您的「姓名」與「手機號碼」，中間記得加上「空格」。\n範例：王小明 0912345678\n\n(若想中斷操作，請點擊選單其他按鈕或輸入「取消」)"

    # === 4. 功能 2 & 3 權限檢查：必須先註冊才能查訂單 ===
    elif user_message in ["即時訂單", "訂單狀態", "進行中訂單", "歷史訂單", "歷史紀錄", "查詢訂單", "訂單查詢", "我的訂單"]:
        is_registered, _, _ = check_user_registered(user_id)
        if not is_registered:
            return "⚠️ 您尚未完成 LINE 綁定，無法查詢。\n\n請先點擊「會員資料」或輸入「登記」，完成綁定後即可使用此功能。"

        # 依據指令呼叫對應的查詢函式
        if user_message in ["即時訂單", "訂單狀態", "進行中訂單"]:
            return query_active_orders(user_id)
            
        elif user_message in ["歷史訂單", "歷史紀錄"]:
            return query_historical_orders(user_id)
            
        elif user_message in ["查詢訂單", "訂單查詢", "我的訂單"]:
            return query_user_orders(user_id)

    else:
        return None  # 非資料庫相關指令，交由 LINE 商用帳號自動回應處理

if __name__ == "__main__":
    print("\n" + "="*50)
    print("🚀 智慧農場 LINE Bot 伺服器啟動程序開始")
    print("="*50)
    
    # 測試資料庫連線
    print("⏳ 正在測試資料庫連線...")
    connection = get_database_connection()
    if connection:
        print("✅ [OK] 資料庫連線成功！")
        connection.close()
    else:
        print("❌ [Error] 資料庫連線失敗，請檢查網路或設定。")
        exit(1)
    
    # 初始化通知佇列資料表
    init_notification_queue()
    # ===== 固定網域版（Cloudflare Tunnel）：已移除 ngrok =====
    # 對外網址由 .env 的 PUBLIC_BASE_URL 指定（你的 worker 對外網域）。
    if PUBLIC_BASE_URL:
        print("*" * 50)
        print(f"🌐 對外網域：{PUBLIC_BASE_URL}")
        print(f"🖼️  QR 圖網址：{PUBLIC_BASE_URL}/order-qr/<檔名>")
        print(f"🎯 LINE Webhook 請在 LINE 後台設為：{PUBLIC_BASE_URL}/botgoodveg/callback（設一次即可，不再變動）")
        print("*" * 50)
    else:
        print("⚠️ 尚未設定 PUBLIC_BASE_URL！請在 .env 設 PUBLIC_BASE_URL=https://你的worker對外網域")
        print("   未設 → QR 圖無法推送（只會推文字）。")

    # 啟動佇列執行緒（每 5 秒輪詢）
    queue_thread = threading.Thread(target=process_notification_queue, daemon=True)
    queue_thread.start()
    print("✅ 通知佇列已啟動。")

    print("\n⚡ 伺服器正在監聽 port 4999... 按下 Ctrl+C 可關閉伺服器。")
    print("="*50 + "\n")

    # 啟動 Flask 伺服器
    app.run(host='0.0.0.0', port=4999, debug=False, use_reloader=False)