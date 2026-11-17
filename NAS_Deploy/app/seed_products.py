# -*- coding: utf-8 -*-
"""一鍵把現有 12 款商品寫入資料庫。

用法（在「連得到資料庫」的那台機器執行）：
    python seed_products.py

特性：
- idempotent：表已經有商品就不會重複塞，可以安全重跑。
- 會自動建立 Products 表（若還沒有）。
- 圖片沿用現有 /img/menu/<款>-1.jpg、-2.jpg，匯入後首頁/點餐頁立刻看得到。
"""
import sys
import app


def main():
    print("連線資料庫並確保商品表存在 / 匯入現有商品…")
    try:
        app.init_db()  # 建表（若無）+ 表為空時塞入 12 款；皆 idempotent
    except Exception as e:
        print(f"\n[失敗] 無法連線或初始化資料庫：{e}")
        print("請確認：(1) 這台連得到 DB_HOST:DB_PORT  (2) .env 帳密正確")
        sys.exit(1)

    conn = app.get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name, name_en, kind, price, is_available FROM Products ORDER BY sort_order, id")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    print(f"\n完成！資料庫目前共有 {len(rows)} 款商品：\n")
    for name, name_en, kind, price, avail in rows:
        tag = f"特殊 +NT${price}" if kind == "special" else "一般萵苣"
        state = "供應中" if avail else "暫無供應"
        print(f"  · {name}（{name_en}）— {tag}、{state}")
    print("\n首頁 / 點餐頁 / 後台「商品管理」現在都會讀到這些商品。")


if __name__ == "__main__":
    main()
