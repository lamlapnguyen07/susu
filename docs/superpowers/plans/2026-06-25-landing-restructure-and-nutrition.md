# 首頁多視圖改版 ＋ 商品營養成分 ＋ 內文放大 — 實作計畫

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推薦）或 superpowers:executing-plans 逐任務實現此計畫。步驟使用複選框（`- [ ]`）語法追蹤進度。

**目標：** 在不動後端商業邏輯、不改文案與視覺風格的前提下，把一頁式首頁改成「單檔多視圖（hash 路由）＋ 視圖內局部切換」，並新增商品「營養成分」欄位、放大首頁內文約 4px。

**架構：**
- 首頁維持單一 `templates/index.html`；把現有 `<section>` 分組包進 5 個 `.view` 容器，由 `landing.js` 依 `location.hash` 一次只顯示一個視圖。視圖內再用通用 tabs／步驟器做局部切換。完全不新增 Flask 路由。
- 「營養成分」是與既有 `features`（營養特色）並存的新自由文字欄位 `nutrition`，貫穿 DB → API → 後台表單 → 前台商品 Modal。
- 內文放大用 `landing.css` 末端一個覆寫區塊集中處理，只動內文/說明類 selector，標題不動。

**技術棧：** Flask + MySQL（`mysql.connector`）、原生 JS（無框架）、原生 CSS。

---

## 測試策略（重要前提）

本專案**目前沒有任何測試框架**（無 `tests/`、`requirements.txt` 無 pytest）。依使用者「不想浪費資源、嚴格範圍」的要求，**本次不導入 pytest／測試資料庫等測試基礎建設**（屬範圍外）。改採：

1. **後端純函式**（`_read_product_fields`、`_product_to_dict`）：用 Python 內建 `unittest` 寫一支不依賴 DB 的輕量檢查（見任務 1）。
2. **其餘（DB 寫入、後台表單、前台視圖/切換、CSS）**：以「實際啟動 app ＋ 操作觀察」驗證，每個任務列出明確的驗證步驟與預期結果。

## Commit 慣例

計畫各任務末尾列出建議的 commit 作為檢查點，但依使用者慣例「**commit 由使用者決定**」，實際執行 commit 前會先向使用者確認；使用者也可選擇全部完成後一次 commit。此 repo 目前**尚無任何 commit**。

## 檔案結構（本次會動到的檔案與職責）

| 檔案 | 職責 | 任務 |
|---|---|---|
| `app.py` | `Products` 表加 `nutrition` 欄位、migration、序列化、INSERT/UPDATE | 1 |
| `templates/admin_products.html` | 後台表單加「營養成分」textarea | 2 |
| `static/js/admin_products.js` | 後台表單收集／回填 `nutrition` | 2 |
| `templates/index.html` | 商品 Modal 加營養成分列；section 包進 5 個 `.view`；nav 改 hash；各視圖局部切換標記 | 3,5,6,7,8,9 |
| `static/js/landing.js` | 商品 Modal 填 `nutrition`；多視圖路由；步驟器；分類篩選；通用 tabs | 3,5,6,7,8,9 |
| `static/css/landing.css` | 內文放大覆寫區塊；視圖顯示/隱藏；tabs/步驟器樣式 | 4,5,6,7,8,9 |
| `tests/test_product_fields.py` | 後端純函式輕量檢查（新建） | 1 |
| `NAS_Deploy/app/**` | 改完同步 | 10 |

---

## 任務 1：後端新增 `nutrition` 欄位（DB ＋ API）

**檔案：**
- 修改：`app.py`（CREATE TABLE `app.py:345`、migration `app.py:333` 後、`_product_to_dict` `app.py:692-701`、`_read_product_fields` `app.py:717-724`、INSERT `app.py:822-827`、UPDATE `app.py:850-855`）
- 測試：`tests/test_product_fields.py`（新建）

- [ ] **步驟 1：寫失敗的測試**

新建 `tests/test_product_fields.py`：

```python
import importlib, sys, types, unittest

# 載入 app.py 但避免匯入時連線 DB：先放一個假的 mysql.connector
sys.modules.setdefault('mysql', types.ModuleType('mysql'))
_conn = types.ModuleType('mysql.connector')
_conn.connect = lambda **k: (_ for _ in ()).throw(RuntimeError('no db in test'))
sys.modules['mysql.connector'] = _conn
sys.modules['mysql'].connector = _conn

app = importlib.import_module('app')


class ProductFieldsTest(unittest.TestCase):
    def test_read_includes_nutrition(self):
        f = app._read_product_fields({'name': 'x', 'nutrition': '維他命B3、鉀'})
        self.assertEqual(f['nutrition'], '維他命B3、鉀')

    def test_read_nutrition_defaults_empty(self):
        f = app._read_product_fields({'name': 'x'})
        self.assertEqual(f['nutrition'], '')

    def test_to_dict_includes_nutrition(self):
        d = app._product_to_dict({'id': 1, 'name': 'x', 'nutrition': '鉀'})
        self.assertEqual(d['nutrition'], '鉀')

    def test_to_dict_nutrition_defaults_empty(self):
        d = app._product_to_dict({'id': 1, 'name': 'x'})
        self.assertEqual(d['nutrition'], '')


if __name__ == '__main__':
    unittest.main()
```

- [ ] **步驟 2：執行測試確認失敗**

執行：`python -m unittest tests/test_product_fields.py -v`
預期：FAIL，`KeyError: 'nutrition'`（函式尚未含此欄位）。
> 若匯入 `app` 因其他相依套件（PIL 等）失敗，表示此環境不適合單元匯入 → 跳過本測試，改用步驟 7 的端對端驗證；但勿因此略過程式碼修改。

- [ ] **步驟 3：CREATE TABLE 加欄位**

`app.py:345`，在 `pairing TEXT,` 之後加一行：

```python
                pairing TEXT,
                nutrition TEXT,
                photo1 VARCHAR(255) DEFAULT '',
```

- [ ] **步驟 4：既有資料庫 migration**

`app.py`，在 `CREATE TABLE IF NOT EXISTS Products (...)` 區塊結束之後、`_seed_products(cur)` 之前，插入：

```python
        # 既有資料庫補欄位（idempotent）
        for stmt in [
            "ALTER TABLE Products ADD COLUMN nutrition TEXT",
        ]:
            try:
                cur.execute(stmt)
            except Exception:
                pass
        _seed_products(cur)
```

（即把原本的 `_seed_products(cur)` 那行，前面補上這個 try/except ALTER 迴圈，模式與 `app.py:311-332` 的 `Order` migration 一致。）

- [ ] **步驟 5：序列化與表單清洗加欄位**

`_product_to_dict`（`app.py:698-699`）在 `"features"` 後加 `nutrition`：

```python
        "description": r.get("description") or "", "features": r.get("features") or "",
        "nutrition": r.get("nutrition") or "",
        "eating": r.get("eating") or "", "pairing": r.get("pairing") or "",
```

`_read_product_fields`（`app.py:720-721`）在 `'features'` 後加 `nutrition`：

```python
        'description': s('description', 2000), 'features': s('features', 2000),
        'nutrition': s('nutrition', 2000),
        'eating': s('eating', 2000), 'pairing': s('pairing', 2000),
```

- [ ] **步驟 6：INSERT / UPDATE 加欄位**

`api_admin_product_create`（`app.py:822-827`）：

```python
        cur.execute(
            "INSERT INTO Products (name,name_en,category,kind,price,description,features,nutrition,eating,pairing,is_available,sort_order)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (f['name'], f['name_en'], f['category'], f['kind'], f['price'],
             f['description'], f['features'], f['nutrition'], f['eating'], f['pairing'], f['is_available'], f['sort_order'])
        )
```

`api_admin_product_update`（`app.py:850-855`）：

```python
        cur.execute(
            "UPDATE Products SET name=%s,name_en=%s,category=%s,kind=%s,price=%s,"
            "description=%s,features=%s,nutrition=%s,eating=%s,pairing=%s,is_available=%s,sort_order=%s WHERE id=%s",
            (f['name'], f['name_en'], f['category'], f['kind'], f['price'],
             f['description'], f['features'], f['nutrition'], f['eating'], f['pairing'], f['is_available'], f['sort_order'], pid)
        )
```

> `_seed_products` 不動：既有 12 款 `nutrition` 由 DB 預設留 NULL → 序列化時轉空字串，符合「不臆造數據」。

- [ ] **步驟 7：執行測試確認通過**

執行：`python -m unittest tests/test_product_fields.py -v`
預期：PASS（4 項）。

- [ ] **步驟 8：Commit（經確認後）**

```bash
git add app.py tests/test_product_fields.py
git commit -m "feat(products): 新增 nutrition（營養成分）欄位與 migration"
```

---

## 任務 2：後台表單「營養成分」欄位

**檔案：**
- 修改：`templates/admin_products.html:118-121`（營養特色欄位後）
- 修改：`static/js/admin_products.js:210`（回填）、`static/js/admin_products.js:250`（收集）

- [ ] **步驟 1：表單加 textarea**

`templates/admin_products.html`，在「營養特色」`f-block`（`:118-121`）之後插入：

```html
      <div class="f-block">
        <label class="f-label" for="f-nutrition">營養成分</label>
        <textarea id="f-nutrition" class="f-input f-area" rows="2" maxlength="2000"
                  placeholder="維他命 B3、維他命 C、鉀、鈣、膳食纖維…"></textarea>
      </div>
```

- [ ] **步驟 2：回填**

`static/js/admin_products.js:210`，在 `f-features` 之後加：

```javascript
  $('f-features').value = isNew ? '' : (p.features || '');
  $('f-nutrition').value = isNew ? '' : (p.nutrition || '');
```

- [ ] **步驟 3：收集**

`static/js/admin_products.js:250`，在 `features:` 之後加 `nutrition:`：

```javascript
    description: $('f-desc').value.trim(), features: $('f-features').value.trim(),
    nutrition: $('f-nutrition').value.trim(),
    eating: $('f-eating').value.trim(), pairing: $('f-pairing').value.trim(),
```

- [ ] **步驟 4：驗證（端對端）**

啟動 app（見任務 10 的啟動方式）→ 進後台商品管理 → 編輯任一商品，填入營養成分「維他命B3、鉀」→ 儲存 → 重新整理 → 重新開啟該商品 → 欄位值仍在。
預期：值正確保留（DB round-trip 成功）。

- [ ] **步驟 5：Commit（經確認後）**

```bash
git add templates/admin_products.html static/js/admin_products.js
git commit -m "feat(admin): 商品表單新增營養成分欄位"
```

---

## 任務 3：前台商品 Modal 顯示營養成分

**檔案：**
- 修改：`templates/index.html:390-403`（`.pm-meta` 內）
- 修改：`static/js/landing.js:22`（normalize）、`static/js/landing.js:100`（填值）

- [ ] **步驟 1：Modal 加一列**

`templates/index.html`，在 `pm-features` 那列（特性）之後、`pm-eating` 之前插入：

```html
          <div class="pm-meta-row" id="pm-nutrition-row">
            <span class="pm-meta-label">營養成分</span>
            <p id="pm-nutrition"></p>
          </div>
```

- [ ] **步驟 2：normalize 帶入欄位**

`static/js/landing.js:22`，在 `features:` 後加：

```javascript
    features: r.features || '',
    nutrition: r.nutrition || '',
```

- [ ] **步驟 3：填值並在空值時隱藏該列**

`static/js/landing.js:100`，在 `pm-features` 設定之後加：

```javascript
  document.getElementById('pm-features').textContent = p.features;
  document.getElementById('pm-nutrition').textContent = p.nutrition;
  document.getElementById('pm-nutrition-row').style.display = p.nutrition ? '' : 'none';
```

- [ ] **步驟 4：驗證**

啟動 app → 首頁商品 → 點有填營養成分的商品 → Modal 顯示「營養成分」列；點未填的商品 → 該列不顯示。
預期：符合上述。

- [ ] **步驟 5：Commit（經確認後）**

```bash
git add templates/index.html static/js/landing.js
git commit -m "feat(landing): 商品 Modal 顯示營養成分"
```

---

## 任務 4：首頁內文放大約 4px

**檔案：**
- 修改：`static/css/landing.css`（檔末新增覆寫區塊）

- [ ] **步驟 1：在 `landing.css` 檔末追加覆寫區塊**

只動內文/說明類 selector（標題、eyebrow、標籤不動）。各值＝原值 +0.25rem（≈4px@16px）：

```css
/* ── 內文字級放大（約 +4px；僅內文/說明，標題不動）── */
.hero-desc      { font-size: 1.17rem; }  /* was .92  */
.section-desc   { font-size: 1.25rem; }  /* was 1    */
.step-content p { font-size: 1.17rem; }  /* was .92  */
.product-desc   { font-size: 1.10rem; }  /* was .85  */
.pillar-desc    { font-size: 1.13rem; }  /* was .88  */
.policy-text    { font-size: 1.20rem; }  /* was .95  */
.value-desc     { font-size: 1.09rem; }  /* was .84  */
.tl-text        { font-size: 1.13rem; }  /* was .88  */
.team-sub       { font-size: 1.20rem; }  /* was .95  */
.pm-desc        { font-size: 1.17rem; }  /* was .92  */
.pm-meta-row p  { font-size: 1.15rem; }  /* was .90  */
```

> 因放在檔末、且未包在 media query 內，會以來源順序覆寫前面（含 `:757-764` 行動裝置內的 `.pm-desc`/`.pm-meta-row p`），行動裝置內文同步變大，符合目標。

- [ ] **步驟 2：驗證**

啟動 app → 首頁桌面與手機寬度（DevTools 響應式）逐視圖檢查：內文明顯變大、標題與版面未跑版；另開 `/pos` 與後台確認字級未受影響（只動 `landing.css`）。
預期：內文放大、其他頁不受影響、無破版。

- [ ] **步驟 3：Commit（經確認後）**

```bash
git add static/css/landing.css
git commit -m "style(landing): 首頁內文字級放大約 4px"
```

---

## 任務 5：首頁多視圖骨架（包視圖 ＋ nav ＋ hash 路由）

**檔案：**
- 修改：`templates/index.html`（包 `.view`、改 nav）
- 修改：`static/js/landing.js`（新增 `setupViews()` 並於 init 呼叫）
- 修改：`static/css/landing.css`（`.view` 顯示/隱藏）

- [ ] **步驟 1：把現有 section 分組包進 5 個 `.view`**

在 `<nav>` 之後、`<footer>` 之前，依下列分組各包一層（不改 section 內部內容，只加外層 `<div class="view" id="..." >`）：

| view 容器 | 包住的現有 section |
|---|---|
| `<div class="view" id="view-home" data-view="home">` | `#hero` |
| `<div class="view" id="view-aquaponics" data-view="aquaponics">` | `#what` |
| `<div class="view" id="view-products" data-view="products">` | `#products` |
| `<div class="view" id="view-about" data-view="about">` | `#about`、`#brand` |
| `<div class="view" id="view-contact" data-view="contact">` | `#contact`、`#policy` |

> 商品 Modal、QR Modal、FAB、footer **維持在所有 view 之外**（全域共用）。

- [ ] **步驟 2：改 nav 連結指向視圖 hash**

`templates/index.html:27-33` 的 `nav-links` 改為（`/pos` CTA 不變）：

```html
  <ul class="nav-links" id="nav-links">
    <li><a href="#home" data-nav="home">首頁</a></li>
    <li><a href="#aquaponics" data-nav="aquaponics">魚菜共生</a></li>
    <li><a href="#products" data-nav="products">當季商品</a></li>
    <li><a href="#about" data-nav="about">關於我們</a></li>
    <li><a href="#contact" data-nav="contact">聯絡訂購</a></li>
    <li><a href="/pos" class="nav-cta">立即訂購 →</a></li>
  </ul>
```

> footer 內 `:429-432` 的錨點連結同步改成 `#aquaponics`/`#products`/`#about`/`/pos`。HERO 內 `href="#what"` 的「了解魚菜共生」按鈕（`:50`）改 `#aquaponics`。

- [ ] **步驟 3：CSS — 預設只顯示 active view**

`landing.css` 覆寫區塊追加：

```css
/* ── 多視圖：一次只顯示一個 ── */
.view { display: none; }
.view.is-active { display: block; }
.nav-links a.is-current { color: var(--ochre-deep); }
```

- [ ] **步驟 4：landing.js 新增 `setupViews()` 路由**

新增函式（放在 `setupNav` 附近）：

```javascript
// ---- 多視圖 hash 路由 ----
const VIEWS = ['home', 'aquaponics', 'products', 'about', 'contact'];

function showView(name) {
  if (!VIEWS.includes(name)) name = 'home';
  document.querySelectorAll('.view').forEach(v => {
    v.classList.toggle('is-active', v.dataset.view === name);
  });
  document.querySelectorAll('#nav-links a[data-nav]').forEach(a => {
    a.classList.toggle('is-current', a.dataset.nav === name);
  });
  // 切換後捲回頂端，並重新觸發該視圖內的淡入
  window.scrollTo({ top: 0 });
  document.querySelectorAll('.view.is-active .fade-up').forEach(el => fadeObserver.observe(el));
}

function currentViewFromHash() {
  return (location.hash || '').replace('#', '') || 'home';
}

function setupViews() {
  if (!document.querySelector('.view')) return;
  showView(currentViewFromHash());
  window.addEventListener('hashchange', () => showView(currentViewFromHash()));
}
```

- [ ] **步驟 5：init 呼叫 `setupViews()`**

`static/js/landing.js:327`，在 `setupNav();` 之後加 `setupViews();`。

- [ ] **步驟 6：驗證**

啟動 app → 點 nav 五個連結逐一切換：一次只顯示一個視圖、網址 hash 同步、nav 當前項高亮；瀏覽器上一頁/下一頁可在視圖間移動；重新整理停在同一視圖；切換後捲動在頂端。商品 Modal、QR、FAB、footer 仍正常。
預期：符合上述。

- [ ] **步驟 7：Commit（經確認後）**

```bash
git add templates/index.html static/js/landing.js static/css/landing.css
git commit -m "feat(landing): 一頁式改為單檔多視圖（hash 路由）"
```

---

## 任務 6：魚菜共生「4 步驟 → 步驟器」

**檔案：** 修改 `templates/index.html`（`#what` 的 `.what-steps`）、`static/js/landing.js`、`static/css/landing.css`

- [ ] **步驟 1：HTML 加步驟器控制列**

在 `.what-steps`（`index.html:104-133`）外層加 `id="aqua-stepper"`，並在其後加上控制列：

```html
  <div class="stepper-controls" id="aqua-controls">
    <button type="button" class="stepper-prev" aria-label="上一步">‹</button>
    <div class="stepper-dots" id="aqua-dots"></div>
    <button type="button" class="stepper-next" aria-label="下一步">›</button>
  </div>
```

- [ ] **步驟 2：CSS — 一次只顯示一步**

```css
/* ── 步驟器 ── */
#aqua-stepper .step { display: none; }
#aqua-stepper .step.is-active { display: flex; }
.stepper-controls { display: flex; align-items: center; gap: 14px; margin-top: 18px; }
.stepper-dots { display: flex; gap: 8px; }
.stepper-dots button { width: 9px; height: 9px; border-radius: 50%; border: 0; background: var(--rule); cursor: pointer; }
.stepper-dots button.is-active { background: var(--ochre-deep); }
.stepper-prev, .stepper-next { width: 34px; height: 34px; border-radius: 50%; border: 1px solid var(--rule); background: var(--card); cursor: pointer; font-size: 18px; }
```

- [ ] **步驟 3：landing.js 步驟器邏輯**

```javascript
// ---- 魚菜共生 4 步驟：步驟器 ----
function setupAquaStepper() {
  const wrap = document.getElementById('aqua-stepper');
  const dots = document.getElementById('aqua-dots');
  if (!wrap || !dots) return;
  const steps = wrap.querySelectorAll('.step');
  if (!steps.length) return;
  let idx = 0;
  steps.forEach((_, i) => {
    const d = document.createElement('button');
    d.type = 'button';
    d.addEventListener('click', () => go(i));
    dots.appendChild(d);
  });
  function go(i) {
    idx = (i + steps.length) % steps.length;
    steps.forEach((s, k) => s.classList.toggle('is-active', k === idx));
    dots.querySelectorAll('button').forEach((b, k) => b.classList.toggle('is-active', k === idx));
  }
  document.querySelector('#aqua-controls .stepper-prev').addEventListener('click', () => go(idx - 1));
  document.querySelector('#aqua-controls .stepper-next').addEventListener('click', () => go(idx + 1));
  go(0);
}
```

於 init（`landing.js:327` 區）加 `setupAquaStepper();`。

> 注意：`.step` 原本有 `fade-up` class（淡入後加 `visible`）。步驟器用 `display:none/flex` 控制顯示，與 `fade-up` 的 opacity 不衝突；若步驟內容因 `fade-up` 未 `visible` 而透明，於步驟啟用時一併補 `el.classList.add('visible')`。

- [ ] **步驟 4：驗證**

啟動 app → 魚菜共生視圖 → 一次只顯示一步、左右鈕與圓點可切換、循環正常、手機不需長捲。
預期：符合上述。

- [ ] **步驟 5：Commit（經確認後）**

```bash
git add templates/index.html static/js/landing.js static/css/landing.css
git commit -m "feat(landing): 魚菜共生 4 步驟改為步驟器"
```

---

## 任務 7：當季商品「一般／特殊」分類切換

**檔案：** 修改 `templates/index.html`（`#products` header）、`static/js/landing.js`（`renderProducts`）、`static/css/landing.css`

- [ ] **步驟 1：HTML 加分類 tab 列**

在 `.products-header`（`index.html:139-145`）之後、`#products-grid` 之前插入：

```html
  <div class="prod-filter" id="prod-filter" role="tablist">
    <button type="button" class="prod-filter-btn is-active" data-kind="all">全部</button>
    <button type="button" class="prod-filter-btn" data-kind="standard">一般萵苣</button>
    <button type="button" class="prod-filter-btn" data-kind="special">特殊品項</button>
  </div>
```

- [ ] **步驟 2：CSS**

```css
/* ── 商品分類切換 ── */
.prod-filter { display: flex; gap: 10px; margin: 4px 0 22px; flex-wrap: wrap; }
.prod-filter-btn { padding: 8px 16px; border-radius: 999px; border: 1px solid var(--rule); background: var(--card); cursor: pointer; font-size: 0.85rem; font-family: var(--font-sans); }
.prod-filter-btn.is-active { background: var(--ochre-deep); color: #fff; border-color: var(--ochre-deep); }
```

- [ ] **步驟 3：`renderProducts` 支援篩選**

把 `renderProducts`（`landing.js:42`）改為依目前篩選顯示，並加 tab 綁定：

```javascript
let PROD_FILTER = 'all';

function renderProducts() {
  const grid = document.getElementById('products-grid');
  if (!grid) return;
  grid.innerHTML = '';
  const list = PRODUCTS.filter(p => PROD_FILTER === 'all' || p.kind === PROD_FILTER);
  if (!list.length) {
    grid.innerHTML = '<p class="products-empty">此分類暫無商品</p>';
    return;
  }
  list.forEach((p) => {
    /* …原本建立 card 的內容不變，迴圈對象由 PRODUCTS 改為 list… */
  });
  document.querySelectorAll('#products-grid .fade-up').forEach(el => fadeObserver.observe(el));
}

function setupProdFilter() {
  const bar = document.getElementById('prod-filter');
  if (!bar) return;
  bar.querySelectorAll('.prod-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      PROD_FILTER = btn.dataset.kind;
      bar.querySelectorAll('.prod-filter-btn').forEach(b => b.classList.toggle('is-active', b === btn));
      renderProducts();
    });
  });
}
```

於 init 加 `setupProdFilter();`（在 `loadProducts()` 前後皆可，`renderProducts` 會用到 `PROD_FILTER` 預設 `'all'`）。

> 僅把 `renderProducts` 內 `PRODUCTS.forEach` 改為 `list.forEach`，card 內部 HTML 與點擊綁定完全不變。

- [ ] **步驟 4：驗證**

啟動 app → 商品視圖 → 切「一般萵苣」只顯示 standard、「特殊品項」只顯示 special、「全部」全顯示；商品 Modal 仍正常開啟。
預期：符合上述。

- [ ] **步驟 5：Commit（經確認後）**

```bash
git add templates/index.html static/js/landing.js static/css/landing.css
git commit -m "feat(landing): 商品加一般/特殊分類切換"
```

---

## 任務 8：關於我們 tabs（含 ESG 預留位）

**檔案：** 修改 `templates/index.html`（`#about`＋`#brand`）、`static/js/landing.js`（通用 tabs）、`static/css/landing.css`

> 本任務把「關於我們」視圖內既有區塊重組為 5 個分頁，**內容文字一字不改，只搬進對應 panel**。ESG 為預留空 panel。

- [ ] **步驟 1：在 `#view-about` 內加 tab 列與 panel 包層**

在 `#view-about` 視圖最上方加 tab 列：

```html
  <div class="about-tabs" id="about-tabs" role="tablist">
    <button type="button" class="about-tab is-active" data-panel="story">品牌故事</button>
    <button type="button" class="about-tab" data-panel="values">永續價值</button>
    <button type="button" class="about-tab" data-panel="timeline">發展歷程</button>
    <button type="button" class="about-tab" data-panel="brand">品牌理念</button>
    <button type="button" class="about-tab" data-panel="esg">永續 ESG</button>
  </div>
```

把既有內容搬進 5 個 panel（`<div class="about-panel" data-panel="...">`）：

| panel | 放入的既有元素 |
|---|---|
| `story` | `#about` 的 `.about-left` 標題＋`.section-desc`（故事文字）＋ `.about-right` 的 `.team-intro` 故事段 |
| `values` | `.about-values`（4 個 value-item） |
| `timeline` | `.team-intro` 內的 timeline-item 群 |
| `brand` | `#brand` 的 `.brand-pillars`（三支柱）＋ `.brand-inner` 標題段 |
| `esg` | **預留**：見步驟 2 占位內容 |

> 搬移時保留各元素原 class，使既有樣式續用；只把它們的父層換成對應 `.about-panel`。

- [ ] **步驟 2：ESG 預留 panel（占位、待補內容）**

```html
  <div class="about-panel" data-panel="esg" hidden>
    <div class="section-tag center"><span class="rule"></span>永續 ESG<span class="rule"></span></div>
    <div class="esg-subtabs" role="tablist">
      <button type="button" class="esg-subtab is-active" data-esg="E">環境 E</button>
      <button type="button" class="esg-subtab" data-esg="S">社會 S</button>
      <button type="button" class="esg-subtab" data-esg="G">治理 G</button>
    </div>
    <div class="esg-pane" data-esg="E"><p class="section-desc">（環境面內容待補）</p></div>
    <div class="esg-pane" data-esg="S" hidden><p class="section-desc">（社會面內容待補）</p></div>
    <div class="esg-pane" data-esg="G" hidden><p class="section-desc">（治理面內容待補）</p></div>
  </div>
```

> 這是唯一刻意的「待補」內容，屬使用者明示延後項；ESG 文案/數據由使用者日後提供後填入。

- [ ] **步驟 3：CSS — tabs 與 panel**

```css
/* ── 通用分頁（關於 / 購買須知共用）── */
.about-tabs, .policy-tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 26px; }
.about-tab, .policy-tab { padding: 9px 18px; border-radius: 999px; border: 1px solid var(--rule); background: var(--card); cursor: pointer; font-size: 0.88rem; font-family: var(--font-sans); }
.about-tab.is-active, .policy-tab.is-active { background: var(--ochre-deep); color: #fff; border-color: var(--ochre-deep); }
.about-panel[hidden], .policy-panel[hidden], .esg-pane[hidden] { display: none; }
.esg-subtabs { display: flex; gap: 8px; margin: 16px 0; }
.esg-subtab { padding: 6px 14px; border-radius: 999px; border: 1px solid var(--rule); background: var(--card); cursor: pointer; font-size: 0.82rem; }
.esg-subtab.is-active { background: var(--moss); color: #fff; border-color: var(--moss); }
```

- [ ] **步驟 4：landing.js 通用 tabs helper**

新增可重用函式（關於與購買須知共用）：

```javascript
// ---- 通用分頁：tabSel 切換 panelSel 的顯示（用 data 屬性對應）----
function setupTabs(tabSel, panelSel, key) {
  const tabs = document.querySelectorAll(tabSel);
  const panels = document.querySelectorAll(panelSel);
  if (!tabs.length || !panels.length) return;
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const v = tab.dataset[key];
      tabs.forEach(t => t.classList.toggle('is-active', t === tab));
      panels.forEach(p => { p.hidden = (p.dataset[key] !== v); });
    });
  });
}
```

於 init 加：

```javascript
  setupTabs('#about-tabs .about-tab', '#view-about .about-panel', 'panel');
  setupTabs('.esg-subtabs .esg-subtab', '#view-about .esg-pane', 'esg');
```

> 初始顯示：第一個 panel（`story`）不加 `hidden`，其餘 4 個 panel 標 `hidden`；ESG 三個 pane 同理。

- [ ] **步驟 5：驗證**

啟動 app → 關於視圖 → 5 個分頁切換正常、各 panel 內容＝原本對應區塊、文字未變動；ESG 分頁顯示 E/S/G 子切換與「待補」占位；版面在桌面與手機皆正常。
預期：符合上述。

- [ ] **步驟 6：Commit（經確認後）**

```bash
git add templates/index.html static/js/landing.js static/css/landing.css
git commit -m "feat(landing): 關於我們改為分頁切換並預留 ESG 位"
```

---

## 任務 9：購買須知 3 條款 tabs

**檔案：** 修改 `templates/index.html`（`#policy`）、`static/js/landing.js`（複用 `setupTabs`）、`static/css/landing.css`

- [ ] **步驟 1：HTML 改為 tab ＋ panel**

`#policy` 的 `.policy-grid`（`index.html:317-352`）改為：tab 列 ＋ 3 個 panel（把 3 張既有 `.policy-card` 各包進一個 `.policy-panel`，卡片內文字不變）：

```html
  <div class="policy-tabs" id="policy-tabs" role="tablist">
    <button type="button" class="policy-tab is-active" data-policy="note">注意事項</button>
    <button type="button" class="policy-tab" data-policy="return">退換貨方式</button>
    <button type="button" class="policy-tab" data-policy="privacy">隱私權條款</button>
  </div>
  <div class="policy-panel" data-policy="note">   <!-- 既有 01 注意事項 article --> </div>
  <div class="policy-panel" data-policy="return" hidden> <!-- 既有 02 退換貨 article --> </div>
  <div class="policy-panel" data-policy="privacy" hidden> <!-- 既有 03 隱私權 article --> </div>
```

- [ ] **步驟 2：landing.js 綁定（複用 `setupTabs`）**

init 加：

```javascript
  setupTabs('#policy-tabs .policy-tab', '#view-contact .policy-panel', 'policy');
```

- [ ] **步驟 3：驗證**

啟動 app → 聯絡訂購視圖 → 購買須知 3 分頁切換正常、各條款文字＝原文；手機端不需長捲即可逐條閱讀。
預期：符合上述。

- [ ] **步驟 4：Commit（經確認後）**

```bash
git add templates/index.html static/js/landing.js static/css/landing.css
git commit -m "feat(landing): 購買須知改為分頁切換"
```

---

## 任務 10：整體回歸 ＋ 同步到 NAS_Deploy

**檔案：** 複製 `app.py`、`templates/index.html`、`templates/admin_products.html`、`static/js/landing.js`、`static/js/admin_products.js`、`static/css/landing.css` → `NAS_Deploy/app/` 對應位置

- [ ] **步驟 1：本機整體回歸**

啟動 app，逐項確認：
- 5 個視圖切換、hash、上一頁/下一頁、重新整理、捲動回頂 ✔
- 魚菜共生步驟器、商品分類切換、關於分頁（含 ESG 占位）、購買須知分頁 ✔
- 商品 Modal 顯示營養成分（空值隱藏列）、後台可存取營養成分 ✔
- 首頁內文已放大、`/pos` 與後台字級未受影響 ✔
- `/api/products` 正常回傳、後台儲存 API 正常、漢堡選單/QR/FAB 正常 ✔

- [ ] **步驟 2：同步檔案到 NAS_Deploy/app**

```bash
cp app.py NAS_Deploy/app/app.py
cp templates/index.html NAS_Deploy/app/templates/index.html
cp templates/admin_products.html NAS_Deploy/app/templates/admin_products.html
cp static/js/landing.js NAS_Deploy/app/static/js/landing.js
cp static/js/admin_products.js NAS_Deploy/app/static/js/admin_products.js
cp static/css/landing.css NAS_Deploy/app/static/css/landing.css
```

- [ ] **步驟 3：驗證兩邊一致**

```bash
md5sum app.py NAS_Deploy/app/app.py
diff -rq templates/ NAS_Deploy/app/templates/
diff -rq static/ NAS_Deploy/app/static/
```

預期：`app.py` 兩邊 MD5 相同；`templates/`、`static/` 無差異輸出。

- [ ] **步驟 4：Commit（經確認後）**

```bash
git add app.py templates static NAS_Deploy docs
git commit -m "chore: 同步首頁改版與營養成分變更到 NAS_Deploy"
```

---

## 自檢結果

- **規格覆蓋度：** 規格三大功能（多視圖／營養成分／內文放大）＋部署同步＋ESG 預留，皆有對應任務（多視圖：5-9；營養成分：1-3；內文放大：4；同步：10；ESG 預留：8 步驟 2）。
- **占位符掃描：** 僅任務 8 的 ESG panel 為刻意占位（使用者明示延後），已標註；其餘步驟皆含實際程式碼與精確路徑/命令。
- **型別/名稱一致性：** `nutrition` 欄位在 DB/序列化/清洗/INSERT/UPDATE/後台 `f-nutrition`/前台 `pm-nutrition` 命名一致；通用 `setupTabs(tabSel, panelSel, key)` 在任務 8、9 以相同簽章呼叫；`PROD_FILTER`、`VIEWS`、`showView` 等命名前後一致。
