# 付款狀態拆分 + 取貨地點（淡水／奇岩）+ LINE 通知差異化 — 設計規格

**日期：** 2026-09-10
**來源：** 操作人員反饋 —— (1) 後台只看得到「付款方式」，看不出錢收了沒；(2) 兩廠取貨，淡水廠在 2 樓，但 LINE 通知寫死「1F大廳」。
**目標範圍：** 子項一 —— 付款方式／狀態拆分、訂單取貨地點、LINE 通知依地點差異化。
**不在範圍內：** 後台代客下單（子項二，另立規格）、LINE 機器人「訂單查詢」訊息顯示地點、掃碼核銷比對地點。

---

## 1. 問題定義

### 1.1 `payment_method` 一欄扛三種語意

現行 `Order.payment_method` 值域為 `unpaid | pending_check | cash | transfer`，同時被當成三種東西用：

| 用途 | 程式位置 | 現況 |
|---|---|---|
| 接單狀態 | `app.py` `api_confirm_order` | 要求 `unpaid`，接單後寫成 `'cash'` — `unpaid→cash` 實際意思是「**接單**」不是「收到現金」 |
| 收款狀態 | `app.py` `api_pay_summary` | 把 `cash`/`transfer` 一律算成「已收款」→ **所有已接單但未取貨的訂單被誤計為已收款**，後台「待收款」數字失真 |
| 付款方式 | `app.py` `_PAY_LABELS`、`admin.js` 下拉 | 出貨單印「未確認」、後台下拉印「未收款」，同值兩種說法 |

操作人員的實際痛點：客人很早就付了現金，後台卻只顯示「現金 ✓」，無法標記「已收款／未收款」。

### 1.2 到貨通知拿不到訂單資料

`app.py` `api_notify` 送出通知時 **不帶 payload**：

```python
enqueue_line_notification("order_arrived", row['customer_phone'])
```

導致 `push_sever.py` 只能寫死一句「請您盡速前往 **1F大廳** 取件」。兩個後果：

1. 要做「淡水廠→2 樓」**一定得**把地點放進 payload，光改文字無效。
2. worker 端是**用電話配對**且訊息不含訂單編號。同一支電話有兩張不同廠的訂單時，會推出兩則一模一樣的訊息，顧客無從分辨該去哪一廠。

### 1.3 品牌名不一致

`push_sever.py` 到貨通知標題為「智慧農場」，下單成功為「食在好蔬」。（`push_sever.py` 第 670 行的「智慧農場」是啟動 log，顧客看不到，不動。）

---

## 2. 資料模型改動

### 2.1 新欄位

沿用既有 idempotent `ALTER TABLE ... ADD COLUMN` + try/except 模式，寫在 `init_db`：

```sql
ALTER TABLE `Order` ADD COLUMN payment_status  VARCHAR(20) DEFAULT 'unpaid';
ALTER TABLE `Order` ADD COLUMN is_confirmed    BOOLEAN     DEFAULT FALSE;
ALTER TABLE `Order` ADD COLUMN pickup_location VARCHAR(20) DEFAULT NULL;

-- payment_method 語意收窄後，預設值不能再是 'unpaid'
ALTER TABLE `Order` MODIFY COLUMN payment_method VARCHAR(20) DEFAULT 'cash';
```

### 2.2 欄位語意（拆分後）

| 欄位 | 值域 | 只負責 |
|---|---|---|
| `payment_method` | `cash`（現金）／`transfer`（轉帳） | **付款方式**。語意收窄，不再出現 `unpaid`／`pending_check` |
| `payment_status` | `unpaid`（未收款）／`pending_check`（待對帳）／`paid`（已收款） | **收款狀態** |
| `is_confirmed` | `0`／`1` | **接單與否**。取代目前用 `payment_method='unpaid'` 判斷接單的做法 |
| `pickup_location` | `tamsui`／`qiyan`／`NULL`（舊訂單） | **取貨地點** |

### 2.3 既有資料遷移

**執行順序有意義**，必須按下表由上而下執行。若第 5 條先跑（`unpaid` → `cash`），第 1、2 條會再抓到這批剛遷好的資料，把 `is_confirmed` 誤設為 1：

| # | 條件（舊值） | `is_confirmed` | `payment_method` | `payment_status` |
|---|---|---|---|---|
| 1 | `payment_method='cash'` **且** `is_picked_up=1` | 1 | `cash` | `paid` |
| 2 | `payment_method='cash'` **且** `is_picked_up=0` | 1 | `cash` | **`unpaid`** |
| 3 | `payment_method='transfer'` | 1 | `transfer` | `paid` |
| 4 | `payment_method='pending_check'` | 1 | `transfer` | `pending_check` |
| 5 | `payment_method='unpaid'` | 0 | `cash` | `unpaid` |

**只跑一次的保護**：在既有的 `site_stats` 表放旗標 `migration_payment_split`（`INSERT IGNORE ... VALUES ('migration_payment_split', 0)`）。`init_db` 讀到 `0` 才執行遷移，成功後寫 `1`。重啟服務不會重跑。

> ⚠️ **必須事先告知操作人員：** 遷移後，目前顯示「現金 ✓（＝已收款）」但**尚未取貨**的訂單，會變成「**現金／未收款**」，後台「待收款」數字會一次跳高。這是把 1.1 的錯誤統計修正回來，不是新 bug。

---

## 3. 取貨地點

### 3.1 設定集中在一處

`app.py` 定義單一常數表，**這是全系統唯一的地點資料源**：

```python
PICKUP_LOCATIONS = {
    'tamsui': {'name': '淡水廠', 'spot': '2 樓大廳', 'label': '淡水廠　2 樓大廳'},
    'qiyan':  {'name': '奇岩廠', 'spot': '1 樓大廳', 'label': '奇岩廠　1 樓大廳'},
}
```

未來加第三個點、或樓層搬動，只改這一份。`push_sever.py` **不持有任何地點知識**（見 §4.1）。

> ✅ 兩廠取貨點已向操作人員確認：**淡水廠 2 樓大廳**、**奇岩廠 1 樓大廳**。（原程式寫死的「1F大廳」只對奇岩廠成立，淡水廠一直是錯的。）

### 3.2 顧客端（`templates/pos.html`、`static/js/pos.js`、`static/css/pos.css`）

- 「填寫取件資訊」區塊，於姓名／電話同組加入**必選**的取貨地點。用 **radio 按鈕**（不用 select）——只有兩個選項，radio 少一次點擊、行動裝置上兩個選項一眼可見，且無預設選中值可強迫顧客主動選擇。
- 未選 → 送出鈕維持 disabled，比照現有姓名／電話的驗證方式。
- 送單 payload 加 `pickup_location`。
- 「訂單已成立」modal 加一列「取貨地點：淡水廠　2 樓大廳」。
- 「我的訂單」抽屜每筆訂單顯示取貨地點。

### 3.3 伺服器端（`app.py`）

- `api_place_order`：驗證 `pickup_location` ∈ `PICKUP_LOCATIONS`，缺漏或非法回 400；寫入 `Order`。
- 新端點 `POST /api/orders/<order_id>/pickup-location`（`@login_required` + `@csrf_required`）：後台修改地點。僅允許 `order_status='active'` 且未取貨的訂單。寫 `audit()`。
- `api_list_orders`：回傳 `pickup_location`；新增 `pickup` 查詢參數供篩選。

### 3.4 後台（`templates/admin.html`、`static/js/admin.js`、`static/css/admin.css`）

- 訂單表格新增「取貨地點」欄，以下拉呈現，可即時修改（比照現有收款下拉的 optimistic update + 失敗回滾模式）。
- 側邊欄篩選加「取貨地點」（全部／淡水／奇岩）。
- 舊訂單 `NULL` 顯示為「未指定」，下拉可補選。

### 3.5 出貨單（`app.py` `_build_shipping_docx`）

- 表頭由 `['訂單編號','姓名','電話','品項','份數','金額','付款','備註','狀態']`
  改為 `['訂單編號','姓名','電話','取貨地點','品項','份數','金額','付款','備註','狀態']`。
- 「付款」欄內容改為「方式／狀態」兩行（例：`現金` ／ `未收款`）。
- 排序在既有 `expected_ship_date, order_time` 之前先加 `pickup_location`，讓同一廠的訂單印在一起，方便分廠備貨。

---

## 4. LINE 通知

### 4.1 技術方案：payload 直接帶「取貨點完整描述文字」

`app.py` 組好完整字串（如 `"淡水廠　2 樓大廳"`）放進 payload，`push_sever.py` 只做字串代入。

**選擇理由：** 兩者是**分開部署的服務**（`NAS_Deploy/app/` 與 `NAS_Deploy/push_server/` 各自一個容器），文案與地點只在 `app.py` 一處維護，push server 完全不需要知道有幾個廠。

已評估但不採用：

- *payload 帶代碼、push server 自持對照表* —— 兩個服務要同步改，必漏。
- *push server 用 order_id 反查 `Order` 表* —— 資料較新（後台改地點後未發送的通知能吃到新值），但多一次跨服務 DB 查詢，且與現行 by-phone 查綁定的架構不一致。列為未來加強選項。

### 4.2 `api_notify`（到貨通知）改動

`enqueue_line_notification` 改為帶 payload：

```python
{
  "order_id": <訂單編號>,
  "pickup_label": PICKUP_LOCATIONS[loc]['label'] if loc else ""   # 舊訂單為空字串
}
```

`api_notify` 的 SELECT 需一併取出 `pickup_location`。

### 4.3 `push_sever.py` — 到貨通知（`order_arrived`）新文案

```
🚚 【食在好蔬 - 到貨通知】

您好 {name}，
您訂購的蔬菜已經到貨囉！
訂單編號：{order_id}

📍 取貨地點：{pickup_label}

請盡速前來取件，享受最新鮮的蔬菜！🥬✨
```

- 品牌名由「智慧農場」改為「**食在好蔬**」，與其他訊息一致。
- `pickup_label` 為空（舊訂單）→ **整個 `📍` 段落與其前後空行一併省略**，其餘文字不變，不會出現「取貨地點：」後面空白。

### 4.4 `push_sever.py` — 下單成功（`order_confirmed`）改動

在「金額」之後插入一行取貨地點；金額後綴不再寫死「（取貨付現）」，改由 payload 的付款方式決定：

```
✅ 【食在好蔬 - 下單成功】

您好 {name}，
您的訂單已確認成立！
訂單編號：{order_id}
金額：NT$ {amount}（{payment_method_text}）
📍 取貨地點：{pickup_label}
預計取貨日：{ship_date_text}

請於上述日期帶著下方的取貨 QR 條碼前來取貨{pay_reminder}。
感謝您的購買！
```

`api_confirm_order` 的 payload 相應增加 `pickup_label` 與 `payment_method_text`（`cash` → `取貨付現`／`transfer` → `轉帳`）。

**尾句必須跟著付款方式走**，否則轉帳單會出現「請前來取貨並付現金」的矛盾指示：

| `payment_method` | `payment_method_text` | `pay_reminder` |
|---|---|---|
| `cash` | `取貨付現` | `並付現金` |
| `transfer` | `轉帳` | （空字串，尾句成為「前來取貨。」） |

`ship_date_text` 為空時維持現行的第二種說法分支，該分支的尾句同樣套用 `pay_reminder`。

---

## 5. 付款拆分的程式改動清單

### 5.1 `app.py`

| 函式 | 改動 |
|---|---|
| `api_place_order` | INSERT 的付款欄位由寫死 `payment_method='unpaid'` 改為 `payment_method='cash'`、`payment_status='unpaid'`、`is_confirmed=FALSE`；一併寫入 `pickup_location` |
| `api_confirm_order` | 條件由 `payment_method='unpaid'` 改為 `is_confirmed=0`；更新語句由 `SET payment_method='cash'` 改為 `SET is_confirmed=TRUE`（**不再碰 `payment_method`**），保留寫 `qr_token` |
| `api_update_payment` | 改吃 `{method, status}`，**兩個都是選填**：只傳 `method` 就只更新方式，只傳 `status` 就只更新狀態，兩個都傳則一起更新，都沒傳回 400。各自驗證值域；維持不推 LINE |
| `api_pay_summary` | 統計改讀 `payment_status`（`unpaid` / `pending_check` / `paid`） |
| `api_list_orders` | `pay_status` 篩選改讀 `payment_status`；新增 `pickup` 篩選 |
| `_PAY_LABELS` | 拆成 `_PAY_METHOD_LABELS = {'cash':'現金','transfer':'轉帳'}` 與 `_PAY_STATUS_LABELS = {'unpaid':'未收款','pending_check':'待對帳','paid':'已收款'}` |

### 5.2 `static/js/admin.js`

- 收款欄：單一下拉 → **方式下拉 + 狀態下拉**並排。各自 `change` 時只送自己那一個欄位（方式下拉送 `{method}`、狀態下拉送 `{status}`）到 `/api/orders/<id>/payment`，互不覆蓋對方的值。
- 「確認訂單」按鈕顯示條件：`o.payment_method === 'unpaid'` → `!o.is_confirmed`。
- 「推播」按鈕顯示條件：`o.payment_method !== 'unpaid'` → `o.is_confirmed`。
- 付款頁籤（全部／待收款／已收款）改送 `payment_status` 值。
- 顏色 class 由 `payment_method` 判斷改為由 `payment_status` 判斷（已收款＝綠、待對帳＝黃、未收款＝無色）。

---

## 6. 部署副本同步

改動完成後，需把根目錄的下列檔案同步覆蓋到 `NAS_Deploy/`：

| 來源 | 目的地 |
|---|---|
| `app.py` | `NAS_Deploy/app/app.py` |
| `push_sever.py` | `NAS_Deploy/push_server/push_sever.py` |
| `templates/` | `NAS_Deploy/app/templates/` |
| `static/` | `NAS_Deploy/app/static/` |

已確認 `NAS_Deploy/app/app.py`（6/16）與根目錄（6/25）的 21 行差異**全部是落後**（缺 `nutrition` 欄位相關改動），無單獨修改，可安全覆蓋。
`上傳nas/` 與 `上傳nas.rar` 是一次性上傳打包產物，**不動**。

---

## 7. 錯誤處理

| 情境 | 行為 |
|---|---|
| 下單未選取貨地點 | 前端 disabled 送出鈕；後端回 400「請選擇取貨地點」 |
| 地點值非法（竄改） | 後端回 400，不寫入 |
| 舊訂單 `pickup_location = NULL` | 後台顯示「未指定」；LINE 通知省略 `📍` 段落；出貨單該欄印「—」 |
| 遷移旗標已為 1 | 跳過遷移，直接啟動 |
| 遷移中途失敗 | 整段包在同一 transaction，`rollback` 後不寫旗標，下次啟動重試；沿用 `init_db` 現行的 try/except + print 模式 |
| 後台改地點時訂單已取貨／已取消 | 回 409，前端下拉回滾原值 |

---

## 8. 測試

現有 `tests/` 只有 `test_product_fields.py`。新增：

1. **`test_payment_migration.py`** — 五條遷移規則各建一筆樣本資料，跑遷移後驗證三欄結果；重複執行 `init_db` 驗證旗標保護（資料不被二次改動）。
2. **`test_pickup_location.py`** — 下單缺地點回 400、非法值回 400、合法值正確寫入；後台改地點成功／已取貨回 409。
3. **`test_notification_payload.py`** — `api_notify` 產生的 payload 含 `order_id` 與正確的 `pickup_label`；`pickup_location=NULL` 時 `pickup_label` 為空字串；`api_confirm_order` 的 payload 依付款方式產出正確的 `payment_method_text` 與 `pay_reminder`（轉帳單不得出現「付現金」字樣）。

**手動驗收**（需真實 LINE 帳號）：淡水單與奇岩單各推一次到貨通知，確認樓層文字正確且含訂單編號；舊訂單（無地點）推一次，確認訊息無殘留空白段落。

---

## 9. 後續子項目（另立規格）

**子項二：後台代客下單** —— 決策已定，實作前另寫規格：

- 新端點 `POST /api/admin/orders`（`@login_required` + `@csrf_required`），不套用顧客端的 LINE 登入強制、每日包數上限、IP 速率限制，但**金額一律伺服器端重算**。
- 商品選擇採**簡化版**：只選組合包數量 + 出貨日 + 取貨地點 + 備註欄手寫特殊需求。不複刻 `pos.js` 的自選萵苣／特殊品項加價邏輯。
- `Order` 加 `created_by VARCHAR(20)`（`customer`／`admin`），列表顯示「代客」標籤。
- **LINE 綁定即時檢查**：輸入電話當下查 `CustomerLineBindings` —— 已綁顯示綠字「✓ 已綁定 LINE，通知正常」；未綁顯示黃底警告「⚠ 此顧客未綁定 LINE，不會收到任何通知與取貨 QR，請自行電話聯繫」，該訂單在列表長期掛「☎ 需電話通知」標記，推播按鈕停用。
  （原因：`push_sever.py` 派送時以電話反查 `CustomerLineBindings`，查不到即標 `failed`，後台目前毫無提示。）
