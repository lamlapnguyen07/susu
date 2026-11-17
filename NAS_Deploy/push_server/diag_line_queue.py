"""
診斷工具：確認「✓收款」後 LINE 推播沒到達的斷點在哪。

用法：python diag_line_queue.py
（需要先設定環境變數 DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME，跟 app.py 相同）
"""
import json
import os
import mysql.connector

DB_CONFIG = {
    'host': os.environ.get('DB_HOST', ''),
    'port': int(os.environ.get('DB_PORT', '3306')),
    'user': os.environ.get('DB_USER', ''),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', 'susu'),
    'charset': 'utf8mb4'
}

def main():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor(dictionary=True)

    print("\n" + "="*70)
    print("【1】 最近 5 筆訂單（按時間排序）")
    print("="*70)
    cur.execute("""
        SELECT order_id, customer_name, customer_phone, payment_method,
               is_picked_up, line_id, order_time
        FROM `Order`
        ORDER BY order_time DESC LIMIT 5
    """)
    rows = cur.fetchall()
    for r in rows:
        print(f"  #{r['order_id']} | {r['customer_name']} ({r['customer_phone']}) "
              f"| pay={r['payment_method']} | picked={bool(r['is_picked_up'])} "
              f"| line_id={'有' if r['line_id'] else '無'} | {r['order_time']}")

    print("\n" + "="*70)
    print("【2】 LineNotificationQueue 最近 10 筆")
    print("="*70)
    cur.execute("""
        SELECT id, type, phone, status, created_at,
               LEFT(payload, 80) AS payload_preview
        FROM LineNotificationQueue
        ORDER BY created_at DESC LIMIT 10
    """)
    rows = cur.fetchall()
    if not rows:
        print("  (空 — 完全沒有 queue 記錄。後端 INSERT 失敗？)")
    else:
        for r in rows:
            status_mark = {
                'pending': '⏳ 等待 worker 抓',
                'done':    '✓ 已推播',
                'failed':  '✗ 失敗',
            }.get(r['status'], r['status'])
            print(f"  #{r['id']:>4} | type={r['type']:<20} | phone={r['phone']} "
                  f"| {status_mark} | {r['created_at']}")
            if r['payload_preview']:
                print(f"         payload: {r['payload_preview']}")

    print("\n" + "="*70)
    print("【3】 CustomerLineBindings 對應檢查")
    print("="*70)
    print("  針對 queue 裡每個 phone，查 CustomerLineBindings 有沒有綁定：")
    cur.execute("""
        SELECT DISTINCT q.phone,
               (SELECT LINE_ID FROM CustomerLineBindings WHERE Phone_Number = q.phone) AS bound_line_id,
               (SELECT Customer_Name FROM CustomerLineBindings WHERE Phone_Number = q.phone) AS bound_name
        FROM LineNotificationQueue q
        ORDER BY q.phone
        LIMIT 20
    """)
    rows = cur.fetchall()
    for r in rows:
        if r['bound_line_id']:
            print(f"  {r['phone']} → 已綁 ({r['bound_name']}, line_id={r['bound_line_id'][:12]}...)")
        else:
            print(f"  {r['phone']} → 未綁  ← 沒綁 LINE，worker 會標 failed")

    print("\n" + "="*70)
    print("【4】 看 type='payment_confirmed' 的 queue 狀態")
    print("="*70)
    cur.execute("""
        SELECT id, phone, status, payload, created_at
        FROM LineNotificationQueue
        WHERE type='payment_confirmed'
        ORDER BY created_at DESC LIMIT 5
    """)
    rows = cur.fetchall()
    if not rows:
        print("  (空 — 你點了「✓收款」但完全沒寫進 queue。後端 bug。)")
    else:
        for r in rows:
            print(f"  #{r['id']} | phone={r['phone']} | status={r['status']} | {r['created_at']}")
            if r['payload']:
                try:
                    p = json.loads(r['payload'])
                    print(f"         payload: order={p.get('order_id')} amt={p.get('amount')} ship={p.get('ship_date')}")
                except Exception:
                    print(f"         payload (raw): {r['payload'][:100]}")

    cur.close()
    conn.close()
    print("\n" + "="*70)
    print("診斷完成。把以上輸出截給 Claude，他會判斷斷在哪。")
    print("="*70)

if __name__ == '__main__':
    main()
