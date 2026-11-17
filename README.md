# 食在好蔬 · Web 版

智慧水耕農場 POS / 後台管理系統 — 從 PyQt6 桌面版改寫為 Flask + HTML/CSS/JS 網頁版。

## 啟動方式

```bash
pip install -r requirements.txt
python app.py
```

啟動後：

| 頁面 | 網址 | 說明 |
|---|---|---|
| 收銀台 | http://localhost:5000/pos | 結帳下單（無需登入） |
| 後台 | http://localhost:5000/admin | 訂單管理（需登入） |
| 登入 | http://localhost:5000/login | 帳密見下方 |

### 後台帳密

帳號密碼不寫在程式或文件裡，請在 `.env` 設定 `ADMIN_USER`、`ADMIN_PASS`（沒設定時後台一律拒絕登入）。

> 閒置 **15 分鐘** 無任何鍵盤/滑鼠操作會自動登出。後台右上角顯示剩餘倒數。

## 資料庫

- 資料庫名稱：`susu`（已在 `app.py` 的 `DB_CONFIG` 設定）
- 啟動時自動執行 schema migration（新增欄位不存在時才加，不會破壞既有資料）

新增欄位：
- `expected_ship_date DATE` — 預計出貨日期，用於後台依日期批次操作
- `total_quantity INT` — 整筆訂單總包數
- 每個 pack 內含 `quantity` 與 `line_total`，存在 `order_details` JSON 內

LINE 通知佇列 `LineNotificationQueue` 完全保留原邏輯。

## 檔案結構

```
食在好蔬/
├── app.py                  # Flask 後端 + API + 認證
├── requirements.txt
├── templates/
│   ├── login.html          # 登入頁
│   ├── pos.html            # 收銀下單頁
│   └── admin.html          # 後台管理頁
├── static/
│   ├── css/
│   │   ├── base.css        # 共用設計系統
│   │   ├── login.css
│   │   ├── pos.css
│   │   └── admin.css
│   └── js/
│       ├── pos.js
│       └── admin.js
└── main.py                 # 舊 PyQt6 版本（保留參考）
```

## 設計系統 — Garden Press

- **色票**：暖奶油底（#f3ecdc）× 深森林綠（#2a4a2c）× 赭橙重點（#b87333）
- **字型**：Fraunces（顯示字，variable serif）+ Manrope（內文 sans）+ Noto Serif/Sans TC
- **質感**：紙張紋理 noise overlay、葉子 SVG 裝飾、雜誌風編號（01 02 03）

避開了一般 SaaS 那種圓角綠按鈕 + 漸層紫卡片 + emoji 滿天飛的 AI 樣板。
