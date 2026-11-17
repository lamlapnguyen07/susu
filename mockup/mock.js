// 模擬稿共用腳本：頁首/頁尾/購物車（正式版會改成 Flask Jinja 的 base.html，伺服器端輸出）
(function () {
  const page = document.body.dataset.page || '';
  const nav = [
    ['shop.html', '市集', 'shop'],
    ['veggies.html', '我們的好蔬', 'veg'],
    ['farm.html', '合作農場', 'farm'],
    ['story.html', '品牌故事', 'story'],
    ['index.html#cycle', '魚菜共生', 'cycle'],
    ['index.html#faq', '取貨與配送', 'faq'],
  ];

  const header = `
  <div class="mock-banner">模擬稿　<span class="tbd">黃底文字</span> 是需要你確認或補上的內容</div>
  <header class="topbar">
    <div class="wrap">
      <a class="brand" href="index.html"><img src="../img/LOGO.png" alt=""><b>食在好蔬</b></a>
      <ul class="menu" id="menu">
        ${nav.map(([h, t, k]) => `<li><a href="${h}"${k === page ? ' aria-current="page"' : ''}>${t}</a></li>`).join('')}
      </ul>
      <div class="top-actions">
        <button class="cart-btn" id="cart-open" type="button" aria-label="開啟購物車">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 6h15l-1.5 9h-12z"/><path d="M6 6 5 2H2"/><circle cx="9" cy="20" r="1.4"/><circle cx="18" cy="20" r="1.4"/></svg>
          <span class="lbl">購物車</span><span class="cart-count" id="cart-count">0</span>
        </button>
        <button class="burger" id="burger" type="button" aria-label="開啟選單" aria-expanded="false">選單</button>
      </div>
    </div>
  </header>`;

  const footer = `
  <footer class="foot">
    <div class="wrap">
      <div class="foot-grid">
        <div class="foot-brand">
          <b>食在好蔬</b>
          <p>臺北市北投區一德里學園路<br>chihaoshu@eatgoodveg.com<br><span class="tbd">電話、取貨時間待補</span></p>
        </div>
        <div><h4>買菜</h4><ul>
          <li><a href="shop.html">全部商品</a></li>
          <li><a href="veggies.html">我們的好蔬</a></li>
          <li><a href="shop.html?src=farm">合作農場商品</a></li>
          <li><a href="product.html">紅橡木萵苣（商品頁範例）</a></li>
        </ul></div>
        <div><h4>認識我們</h4><ul>
          <li><a href="story.html">品牌故事</a></li>
          <li><a href="index.html#cycle">魚菜共生怎麼運作</a></li>
          <li><a href="farm.html">合作農場</a></li>
          <li><a href="#">成為合作農場</a></li>
        </ul></div>
        <div><h4>購物說明</h4><ul>
          <li><a href="index.html#faq">取貨與配送</a></li>
          <li><a href="#">退換貨</a></li>
          <li><a href="#">隱私權政策</a></li>
          <li><a href="https://lin.ee/PJHXHVrQ" rel="noopener">加入 LINE 好友</a></li>
        </ul></div>
      </div>
      <div class="foot-bottom">© 食在好蔬 Good Veggie</div>
    </div>
  </footer>
  <aside class="drawer" id="drawer" aria-label="購物車" aria-hidden="true">
    <div class="drawer-head"><h2>購物車</h2><button class="drawer-close" id="cart-close" type="button" aria-label="關閉購物車">×</button></div>
    <div class="drawer-body" id="drawer-body"></div>
    <div class="drawer-foot">
      <div class="drawer-total"><span>小計</span><span id="cart-total">NT$ 0</span></div>
      <p class="drawer-note">蔬菜和合作農場的商品一次結帳。<span class="tbd">運費與合併出貨規則待定</span></p>
      <a class="btn btn-main" href="#">前往結帳</a>
    </div>
  </aside>
  <div class="drawer-bg" id="drawer-bg"></div>`;

  document.body.insertAdjacentHTML('afterbegin', header);
  document.body.insertAdjacentHTML('beforeend', footer);

  const $ = (id) => document.getElementById(id);
  const burger = $('burger'), menu = $('menu');
  burger.addEventListener('click', () => {
    const open = menu.classList.toggle('open');
    burger.setAttribute('aria-expanded', open);
  });

  // ── 購物車（記憶體內，示意用） ──
  const cart = [];
  const drawer = $('drawer'), bg = $('drawer-bg');
  function openCart() { drawer.classList.add('open'); bg.classList.add('show'); drawer.setAttribute('aria-hidden', 'false'); $('cart-close').focus(); }
  function closeCart() { drawer.classList.remove('open'); bg.classList.remove('show'); drawer.setAttribute('aria-hidden', 'true'); }
  $('cart-open').addEventListener('click', openCart);
  $('cart-close').addEventListener('click', closeCart);
  bg.addEventListener('click', closeCart);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCart(); });

  function render() {
    const count = cart.reduce((s, x) => s + x.qty, 0);
    $('cart-count').textContent = count;
    const total = cart.reduce((s, x) => s + x.qty * x.price, 0);
    $('cart-total').textContent = 'NT$ ' + total.toLocaleString();
    if (!cart.length) { $('drawer-body').innerHTML = '<p class="drawer-empty">購物車是空的。到市集挑這週採收的菜吧。</p>'; return; }
    const groups = {};
    cart.forEach((x) => { (groups[x.src] = groups[x.src] || []).push(x); });
    $('drawer-body').innerHTML = Object.entries(groups).map(([src, items]) => `
      <div class="drawer-group"><h3>${src}</h3>
        ${items.map((x) => `<div class="drawer-item"><span>${x.name} × ${x.qty}</span><span>NT$ ${(x.qty * x.price).toLocaleString()}</span></div>`).join('')}
      </div>`).join('');
  }
  window.addToCart = function (name, price, src, qty) {
    const hit = cart.find((x) => x.name === name);
    if (hit) hit.qty += qty || 1; else cart.push({ name, price: Number(price), src, qty: qty || 1 });
    render();
  };
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-add]');
    if (!b) return;
    window.addToCart(b.dataset.add, b.dataset.price, b.dataset.src);
    b.classList.add('done'); b.textContent = '已加入';
    setTimeout(() => { b.classList.remove('done'); b.textContent = '加入購物車'; }, 1400);
  });
  render();
})();
