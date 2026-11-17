# 首頁改版 ＋ 商品營養成分欄位 ＋ 內文放大 — 設計規格

- 日期：2026-06-25
- 專案：食在好蔬 Good Veggie（Flask + MySQL）
- 狀態：已與使用者確認，待寫實作計畫

## 一、範圍

**本次要做：**

1. 首頁一頁式 → **單檔多視圖**（hash 路由，混合型：多視圖 ＋ 視圖內局部切換）
2. 後台商品新增**「營養成分」單一文字欄位**（與既有「營養特色」不同的新欄位）
3. 前台首頁**內文字放大約 4px**（只前台首頁內文，標題不動）

**本次不做（之後再補）：**

- ESG 內容：版面**預留位置**，文案／數據由使用者日後提供後再填。

**全程不變：** 不改後端商業邏輯、不改既有文案、不改既有視覺風格。restructure 只是重組「呈現方式」。

---

## 二、功能一：首頁單檔多視圖

### 做法選型

採 **A 方案：單檔多視圖（hash 路由）**。維持單一 `templates/index.html`，由 `landing.js` 控制「一次只顯示一個主視圖」，網址以 `#home`／`#aquaponics`… 標示。理由：內容量小、不需為 SEO 拆真實路由、完全不動後端、工作量與風險最低，且照樣達成「手機端不再無止盡下滑」。

### 視圖結構（5 個主視圖）

| 視圖 | hash | 內容（沿用現有區塊） | 視圖內局部切換 |
|---|---|---|---|
| ① 首頁 | `#home` | HERO 主視覺 ＋ 主 CTA | — |
| ② 魚菜共生 | `#aquaponics` | 循環原理圖 ＋ 4 步驟 | 4 步驟改「步驟器」：一次顯示一步、可左右切換 |
| ③ 當季商品 | `#products` | 商品清單 ＋ 商品詳情 Modal | 一般萵苣／特殊品項 分類切換 |
| ④ 關於我們 | `#about` | 故事／價值主張／時間軸／品牌理念 | 頁籤切換；**保留一個「永續 ESG」頁籤位**（之後補內容） |
| ⑤ 聯絡・訂購 | `#contact` | 聯絡資訊 ＋ QR ＋ 購買須知 | 注意事項／退換貨／隱私 三條款 頁籤 |

> 對應現有 `index.html` 區塊：`#hero`→①；`#what`→②；`#products`→③；`#about`＋`#brand`→④；`#contact`＋`#policy`→⑤。原有 section id 可保留為視圖容器或內部錨點。

### 導覽與運作機制

- 導覽列固定在頂端，5 個連結對應 5 個主視圖。
- 點連結 → JS 顯示對應視圖、隱藏其餘；更新網址 hash。
- 支援瀏覽器上一頁／下一頁（`hashchange`）、重新整理後停在同一視圖（讀取目前 hash）、切換後捲動回頂端。
- 無對應 hash（或 `#` / 空）時預設顯示 ① 首頁。
- 既有的「回到頂部」FAB、頁尾、QR Modal、商品 Modal 全部保留。
- 行動裝置漢堡選單沿用現有 `nav-burger` 行為。

### 邊界與限制

- 不新增 Flask 路由、不改 `app.py`。
- 不改任何區塊的文字內容與視覺樣式；只動：顯示/隱藏切換、頁籤、步驟器與必要的版面樣式。
- 視圖切換用 CSS class 控制顯示，避免重繪整頁。

### 影響檔案

- `templates/index.html`（加視圖容器、頁籤、步驟器標記）
- `static/js/landing.js`（hash 路由、視圖切換、頁籤、步驟器）
- `static/css/landing.css`（視圖顯示/隱藏、頁籤、步驟器樣式）

---

## 三、功能二：商品「營養成分」欄位

新增一個**單一自由文字**欄位 `nutrition`，與既有的 `features`（營養特色，描述性文字）並存且用途不同 —— `nutrition` 用來列具體成分，例：「維他命 B3、維他命 C、鉀、鈣、膳食纖維」。

### 資料層（`app.py`，MySQL / `mysql.connector`）

- `Products` 建表語句新增 `nutrition TEXT`（接在 `pairing` 之後）。
- **既有資料庫 migration**：啟動時對既有 `Products` 表執行「若無 `nutrition` 欄位則 `ALTER TABLE Products ADD COLUMN nutrition TEXT`」，確保已部署的資料庫也會補上欄位。
- `_product_to_dict(r)`：序列化加入 `"nutrition": r.get("nutrition") or ""`。
- `_read_product_fields(data)`：加入 `'nutrition': s('nutrition', 2000)`（沿用現有長度上限模式）。
- 建立 API（`api_admin_product_create`）INSERT 欄位清單與值加入 `nutrition`。
- 更新 API（`api_admin_product_update`）UPDATE SET 加入 `nutrition=%s`。
- `_seed_products`：seed 既有 12 款時 `nutrition` 留空字串（不臆造數據）。

### 後台表單（`templates/admin_products.html` ＋ `static/js/admin_products.js`）

- 在「營養特色」欄位之後，新增一個「營養成分」`textarea`（`id="f-nutrition"`），placeholder 範例：「維他命 B3、維他命 C、鉀、鈣、膳食纖維」。
- `admin_products.js`：送出時收集 `nutrition`、開啟編輯時回填 `nutrition`。

### 前台顯示（`templates/index.html` ＋ `static/js/landing.js`）

- 商品詳情 Modal（`.pm-meta`）新增一列「營養成分」，由 `landing.js` 從商品資料的 `nutrition` 帶入；該欄為空時整列隱藏。

### 影響檔案

- `app.py`、`templates/admin_products.html`、`static/js/admin_products.js`、`templates/index.html`、`static/js/landing.js`、（必要時 `static/css/landing.css` 加一列樣式）

---

## 四、功能三：前台首頁內文放大約 4px

- 範圍：**只前台首頁（landing）的內文／說明文字**，標題不變。
- 做法：在 `static/css/landing.css` 調整內文相關 selector 的 `font-size`（段落／說明類，如 `.hero-desc`、`.section-desc`、`.step p`、`.pillar-desc`、`.policy-text`、商品卡與 Modal 描述等）約 +4px；標題（`.hero-h1`、`.section-h2` 等）維持。
- 不動 `base.css` 全域字級，避免波及 POS 點餐頁與後台。

### 影響檔案

- `static/css/landing.css`

---

## 五、部署同步

根目錄與 `NAS_Deploy/app/` 目前位元組完全相同（已比對）。本次改完後，需把下列同步複製到 `NAS_Deploy/app/` 對應位置，保持兩邊一致：

- `app.py`
- `templates/index.html`、`templates/admin_products.html`
- `static/js/landing.js`、`static/js/admin_products.js`
- `static/css/landing.css`

（此步驟列入實作計畫最後一關。）

---

## 六、驗證方式

- **商品營養成分**：後台新增/編輯商品填入 `nutrition` → 儲存 → 重新載入 → 值有保留；前台商品 Modal 正確顯示，空值時不顯示該列。既有資料庫啟動後欄位自動補上、舊商品不報錯。
- **首頁多視圖**：5 個視圖切換正常、網址 hash 同步、上一頁/下一頁與重新整理停在同視圖、行動裝置每個視圖約一屏不需長捲；既有 QR/商品 Modal、FAB、漢堡選單正常。
- **內文放大**：首頁內文明顯變大、標題與版面未跑版；POS 與後台字級未受影響。
- 後端 `/api/products` 仍正常回傳、後台儲存 API 正常。

---

## 七、YAGNI / 排除項

- 不做真・多頁路由、不做 SSR、不做 SEO 結構化資料。
- 營養成分不做結構化（成分名＋含量）資料表，維持單一文字欄。
- ESG 本次只預留版位，不產內容。
- 不重構與本次無關的程式。
