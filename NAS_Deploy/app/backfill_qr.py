"""
補生成 QRCode：幫「已收款（cash/transfer）但還沒有 QR 圖」的舊訂單補上 QR。

用途：在加入 QR 功能之前就已經確認收款的訂單，不會自動有 QR，跑這個一次補齊。

⚠ 先重啟過 app.py 一次（讓 init_db 把 qr_token 欄位加好），再跑這支。

用法：python backfill_qr.py
"""
import os
import secrets
import mysql.connector

# 從環境變數讀 DB（跟 app.py 一致）；主機、帳號、密碼一律放 .env，不寫在程式裡
DB_CONFIG = {
    'host':     os.environ.get('DB_HOST', ''),
    'port':     int(os.environ.get('DB_PORT', '3306')),
    'user':     os.environ.get('DB_USER', ''),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', 'susu'),
    'charset':  'utf8mb4',
}
ORDER_QR_DIR = os.environ.get('ORDER_QR_DIR', '訂單')


def generate_order_qr(order_id, token):
    try:
        import qrcode
        os.makedirs(ORDER_QR_DIR, exist_ok=True)
        img = qrcode.make(f"{order_id}|{token}")
        # 檔名與 app.py 一致：<order_id>_<token>.png（不可猜，供 /order-qr 公開路由）
        img.save(os.path.join(ORDER_QR_DIR, f"{order_id}_{token}.png"))
        return True
    except Exception as e:
        print(f"  ! 生成失敗 {order_id}: {e}")
        return False


def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)

    # 確認 qr_token 欄位存在
    try:
        cur.execute("SELECT qr_token FROM `Order` LIMIT 1")
        cur.fetchall()
    except mysql.connector.Error:
        print("✗ DB 還沒有 qr_token 欄位 — 請先重啟一次 app.py（讓 init_db 加欄位）再跑本腳本。")
        cur.close(); conn.close()
        return

    # 找「已收款 + 還沒 qr_token」的訂單
    cur.execute("""
        SELECT order_id FROM `Order`
        WHERE payment_method IN ('cash','transfer')
          AND (qr_token IS NULL OR qr_token = '')
          AND order_status = 'active'
    """)
    rows = cur.fetchall()
    if not rows:
        print("✓ 沒有需要補生成的訂單（所有已收款訂單都已有 QR）。")
        cur.close(); conn.close()
        return

    print(f"找到 {len(rows)} 筆已收款但沒 QR 的訂單，開始補生成…\n")
    ok = 0
    for r in rows:
        oid = str(r['order_id'])
        token = secrets.token_urlsafe(8)
        cur.execute("UPDATE `Order` SET qr_token=%s WHERE order_id=%s", (token, oid))
        if generate_order_qr(oid, token):
            ok += 1
            print(f"  ✓ #{oid}")
    conn.commit()
    cur.close()
    conn.close()
    print(f"\n完成 — 補生成 {ok}/{len(rows)} 筆，QR 圖存於「{ORDER_QR_DIR}」資料夾。")


if __name__ == '__main__':
    main()
