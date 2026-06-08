// CASES V2 — экономическая симуляция (проект, НЕ влияет на прод).
//
// Запуск:  node scripts/cases_v2_sim.mjs
//
// Зачем: до внедрения Кейсов V2 проверить РЕАЛЬНУЮ экономику — RTP, потоки
// Premium/лимиток/джекпотов, средние потери игрока и окупаемость относительно
// дневного дохода Возни. Все константы взяты из app/settings/balance.py и
// канонических Stars-значений каталога (см. ниже). Монте-Карло + точный RTP.
//
// Источник истины (balance.py):
//   ESHKI_PER_STAR = 10, ITEM_SELL_RATE = 0.70.
// Канонические Stars подарков (присланы пользователем):
//   15: Сердечко, Мишка; 25: Подарок, Роза; 50: Торт, Букет, Ракета, Шампанское;
//   100: Кубок, Кольцо, Бриллиант.
// Лимитки: 1.5× от МАГАЗИННОЙ цены 50★-подарка (700), а не от 50★×10.
//   700 × 1.5 = 1050 full → sell floor(1050×0.70) = 735.
// Каждая из 8 лимиток — ОТДЕЛЬНАЯ награда (свой item_code, вес, вероятность,
// статистика). В дроп-листах разворачиваются в 8 строк равного веса.
// Premium: ТОЛЬКО 3м (1000★) и 6м (1800★). 12м и прочие сроки исключены.

const ESHKI_PER_STAR = 10;
const SELL = 0.70;

const fullFromStars = (s) => s * ESHKI_PER_STAR;
const sellFromStars = (s) => Math.floor(fullFromStars(s) * SELL);

// Магазинная цена 50★-подарка (источник истины каталога) и наценка лимитки.
const SHOP_PRICE_50 = 700;
const LIMITED_MULT = 1.5;
const LIMITED_FULL = Math.round(SHOP_PRICE_50 * LIMITED_MULT); // 1050
const LIMITED_SELL = Math.floor(LIMITED_FULL * SELL);          // 735

// 8 канонических лимиток (миграции 0029/0030): code → имя. Каждая — отдельная
// награда. Ценность одинакова (1050/735), но статистика выпадений раздельная.
const LIMITEDS = [
  { code: "gift_xmas_bear", name: "Рождественский мишка" },
  { code: "gift_xmas_tree", name: "Рождественская ёлка" },
  { code: "gift_valentine_bear", name: "Валентинов мишка" },
  { code: "gift_valentine_heart", name: "Сердце с цветами" },
  { code: "gift_spring_bear", name: "Весенний мишка" },
  { code: "gift_lucky_bear", name: "Счастливый мишка" },
  { code: "gift_clown_bear", name: "Мишка-клоун" },
  { code: "gift_easter_bear", name: "Пасхальный мишка" },
];


// Ценности наград в ешках. Для подарков/Premium берём sell-value (реализуемая
// в закрытую экономику ценность) — консервативная оценка возврата для RTP.
const V = {
  cash: (n) => n,
  heart: sellFromStars(15),     // 105
  bear: sellFromStars(15),      // 105
  gift: sellFromStars(25),      // 175
  rose: sellFromStars(25),      // 175
  cake: sellFromStars(50),      // 350
  bouquet: sellFromStars(50),   // 350
  rocket: sellFromStars(50),    // 350
  champ: sellFromStars(50),     // 350
  cup: sellFromStars(100),      // 700
  ring: sellFromStars(100),     // 700
  diamond: sellFromStars(100),  // 700
  limited: LIMITED_SELL,        // 1.5× магазинной 700 = 1050 → sell 735
  prem3: sellFromStars(1000),   // 7000
  prem6: sellFromStars(1800),   // 12600
};


// Категории для подсчёта потоков.
const CAT = {
  EMPTY: "empty",
  CASH: "cash",
  GIFT: "gift",        // обычный Telegram Gift
  LIMITED: "limited",
  PREM3: "prem3",
  PREM6: "prem6",
  JACKPOT: "jackpot",  // денежный мега-приз с флагом джекпота
};

// --- Определение 6 кейсов V2 -------------------------------------------------
// Веса нормированы на сумму 10000. value — ешки (см. V). cat — для потоков.
// Цены подобраны мягче (см. отчёт по доходу). RTP-цели (по фидбэку):
//   Новичок 0.92–0.95, Фармер 0.94–0.97, Охотник 0.88–0.92,
//   Коллекционер 0.85–0.90, Premium 0.82–0.88, Джекпот 0.80–0.85.
// «Пустых» исходов минимум: вместо 0 — символический возврат/слабый предмет.

// Хелпер: лимитки — 8 ОТДЕЛЬНЫХ наград (по одной на каждый из 8 ID), у каждой
// ЦЕЛЫЙ вес perW (схема case_rewards требует integer weight > 0). Суммарный
// «пул» = perW × 8. У каждой лимитки свой item_code, вес, вероятность и
// статистика. mult — кратность (×1/×2/×3).
function limitedPool(perW, { mult = 1, jackpot = false } = {}) {
  return LIMITEDS.map((g) => ({
    label: mult > 1 ? `${g.name} ×${mult}` : g.name,
    code: g.code,
    value: LIMITED_SELL * mult,
    cat: CAT.LIMITED,
    w: perW,
    mult,
    jackpot,
  }));
}


const CASES = [
  {
    id: "newbie", name: "🐣 Новичок", price: 50,
    rewards: [
      { label: "18 ешек",        value: 18,  cat: CAT.CASH,    w: 3600 },
      { label: "35 ешек",        value: 35,  cat: CAT.CASH,    w: 2700 },
      { label: "55 ешек",        value: 55,  cat: CAT.CASH,    w: 1900 },
      { label: "85 ешек",        value: 85,  cat: CAT.CASH,    w: 1050 },
      { label: "130 ешек",       value: 130, cat: CAT.CASH,    w: 450  },
      { label: "Сердечко 15★",   value: V.heart, cat: CAT.GIFT, w: 250  },
      // Лимитки — очень редкая удача: 8 ID по 6 (суммарно ~0.48%).
      ...limitedPool(6),
    ],
  },

  {
    id: "farmer", name: "🌾 Фармер", price: 150,
    rewards: [
      { label: "70 ешек",   value: 70,   cat: CAT.CASH, w: 2700 },
      { label: "110 ешек",  value: 110,  cat: CAT.CASH, w: 3000 },
      { label: "160 ешек",  value: 160,  cat: CAT.CASH, w: 2300 },
      { label: "220 ешек",  value: 220,  cat: CAT.CASH, w: 1300 },
      { label: "380 ешек",  value: 380,  cat: CAT.CASH, w: 470  },
      { label: "Мишка 15★", value: V.bear, cat: CAT.GIFT, w: 90 },
      ...limitedPool(6),
      { label: "Premium 3м",value: V.prem3, cat: CAT.PREM3, w: 5 },

    ],
  },

  {
    id: "hunter", name: "🎯 Охотник", price: 400,
    rewards: [
      { label: "200 ешек",      value: 200, cat: CAT.CASH, w: 1850 },
      { label: "320 ешек",      value: 320, cat: CAT.CASH, w: 2600 },
      { label: "550 ешек",      value: 550, cat: CAT.CASH, w: 1700 },
      { label: "Сердечко 15★",  value: V.heart, cat: CAT.GIFT, w: 1300 },
      { label: "Подарок 25★",   value: V.gift,  cat: CAT.GIFT, w: 1200 },
      { label: "Торт 50★",      value: V.cake,  cat: CAT.GIFT, w: 800 },
      { label: "Кольцо 100★",   value: V.ring,  cat: CAT.GIFT, w: 380 },
      { label: "1300 ешек",     value: 1300, cat: CAT.CASH, w: 432 },

      ...limitedPool(6),
      { label: "Premium 3м",    value: V.prem3, cat: CAT.PREM3, w: 5 },
    ],
  },

  {
    id: "collector", name: "🏺 Коллекционер", price: 800,
    rewards: [
      { label: "200 ешек",   value: 200,  cat: CAT.CASH, w: 1400 },
      { label: "420 ешек",   value: 420,  cat: CAT.CASH, w: 2700 },
      { label: "700 ешек",   value: 700,  cat: CAT.CASH, w: 1900 },
      { label: "Кольцо 100★",value: V.ring, cat: CAT.GIFT, w: 900 },
      // ГЛАВНЫЙ источник лимиток: 8×250=2000 (×1) + 8×40=320 (×2).
      ...limitedPool(250),
      { label: "1500 ешек",  value: 1500, cat: CAT.CASH, w: 700 },

      ...limitedPool(40, { mult: 2 }),

      { label: "Premium 3м", value: V.prem3, cat: CAT.PREM3, w: 80 },
    ],
  },
  {
    id: "premium", name: "⭐ Premium", price: 1500,
    rewards: [
      { label: "450 ешек",    value: 450,  cat: CAT.CASH, w: 3000 },
      { label: "900 ешек",    value: 900,  cat: CAT.CASH, w: 2950 },
      { label: "1600 ешек",   value: 1600, cat: CAT.CASH, w: 2100 },
      ...limitedPool(113),

      { label: "3500 ешек",   value: 3500, cat: CAT.CASH, w: 720 },

      // Premium СНИЖЕН ~37%: 3м 4.3%→2.7%, 6м 0.7%→0.4% (фидбэк ревизии).
      { label: "Premium 3м",  value: V.prem3, cat: CAT.PREM3, w: 270 },
      { label: "Premium 6м",  value: V.prem6, cat: CAT.PREM6, w: 40 },
    ],
  },

  {
    id: "jackpot", name: "💎 Джекпот", price: 2500,
    rewards: [
      { label: "700 ешек",    value: 700,   cat: CAT.CASH, w: 2980 },
      { label: "1500 ешек",   value: 1500,  cat: CAT.CASH, w: 2900 },
      { label: "2500 ешек",   value: 2500,  cat: CAT.CASH, w: 2100 },
      // ВТОРОЙ источник лимиток: 8×88=704 (×1) + 8×19=152 (×3, джекпот-флаг).
      ...limitedPool(88),
      { label: "6000 ешек",   value: 6000,  cat: CAT.CASH, w: 820 },
      // Premium СНИЖЕН ~36%: 3м 4.0%→2.6%, 6м 1.5%→0.7% (фидбэк ревизии).
      { label: "Premium 3м",  value: V.prem3, cat: CAT.PREM3, w: 260 },
      { label: "Premium 6м",  value: V.prem6, cat: CAT.PREM6, w: 70, jackpot: true },
      ...limitedPool(19, { mult: 3, jackpot: true }),

      { label: "Джекпот 25000",value: 25000, cat: CAT.JACKPOT, w: 20, jackpot: true },

    ],
  },
];



// --- Точный RTP и аналитические шансы ---------------------------------------
function analytics(c) {
  const total = c.rewards.reduce((s, r) => s + r.w, 0);
  let ev = 0;
  const cats = {};
  for (const r of c.rewards) {
    const p = r.w / total;
    ev += p * r.value;
    cats[r.cat] = (cats[r.cat] || 0) + p;
  }
  return { total, ev, rtp: ev / c.price, cats };
}

// --- Монте-Карло -------------------------------------------------------------
function buildCumulative(c) {
  const total = c.rewards.reduce((s, r) => s + r.w, 0);
  let acc = 0;
  return c.rewards.map((r) => {
    acc += r.w;
    return { ...r, cum: acc / total };
  });
}

function simulate(c, n) {
  const cum = buildCumulative(c);
  let payout = 0;
  const counts = { prem3: 0, prem6: 0, limitedItems: 0, jackpot: 0, gift: 0 };
  // Раздельная статистика по каждому из 8 ID лимиток (в штуках с учётом mult).
  const limitedByCode = Object.fromEntries(LIMITEDS.map((g) => [g.code, 0]));
  for (let i = 0; i < n; i++) {
    const x = Math.random();
    let r = cum[cum.length - 1];
    for (const cand of cum) { if (x < cand.cum) { r = cand; break; } }
    payout += r.value;
    if (r.cat === CAT.PREM3) counts.prem3++;
    else if (r.cat === CAT.PREM6) counts.prem6++;
    else if (r.cat === CAT.LIMITED) {
      const m = r.mult || 1;
      counts.limitedItems += m;
      if (r.code) limitedByCode[r.code] += m;
    }
    else if (r.cat === CAT.JACKPOT) counts.jackpot++;
    else if (r.cat === CAT.GIFT) counts.gift++;
  }
  const spent = c.price * n;
  return { n, spent, payout, rtp: payout / spent, netLossPerOpen: (spent - payout) / n, counts, limitedByCode };
}


// --- Дневной доход Возни (для окупаемости) ----------------------------------
// Из balance.py: ферма EV≈6.26/прокрут (cd 4ч), клад 20–50 (2–4/день),
// казино EV≈0.98 (не доход). Профили активности:
function farmEV() {
  const o = [
    { min: 3, max: 8, w: 45 }, { min: 9, max: 15, w: 22 },
    { min: 16, max: 20, w: 8 }, { min: 0, max: 0, w: 15 },
    { min: -5, max: -1, w: 10 },
  ];
  const tw = o.reduce((s, x) => s + x.w, 0);
  return o.reduce((s, x) => s + (x.w / tw) * ((x.min + x.max) / 2), 0);
}
const FARM_EV = farmEV();           // ≈ 6.26
const TREASURE_AVG = (20 + 50) / 2; // 35
const profiles = {
  "casual (1 ферма, 1 клад/д)":   1 * FARM_EV + 1 * TREASURE_AVG,
  "active (3 фермы, 2 клада/д)":   3 * FARM_EV + 2 * TREASURE_AVG,
  "hardcore (6 ферм, 4 клада/д)":  6 * FARM_EV + 4 * TREASURE_AVG,
};

// --- Вывод -------------------------------------------------------------------
function pct(x) { return (x * 100).toFixed(3) + "%"; }
function f0(x) { return Math.round(x).toLocaleString("ru-RU"); }

console.log("=== БАЗОВЫЕ ЦЕННОСТИ (ешки, sell-value) ===");
console.log(`15★=${sellFromStars(15)}  25★=${sellFromStars(25)}  50★=${sellFromStars(50)}  100★=${sellFromStars(100)}`);
console.log(`Лимитка(50★×1.5)=${V.limited}  Premium3м=${V.prem3}  Premium6м=${V.prem6}`);
console.log("");

console.log("=== ДНЕВНОЙ ДОХОД (ешки/день) ===");
console.log(`Ферма EV/прокрут ≈ ${FARM_EV.toFixed(2)}, клад avg = ${TREASURE_AVG}`);
for (const [k, v] of Object.entries(profiles)) console.log(`  ${k}: ${v.toFixed(1)} ешек/день`);
console.log("");

console.log("=== ОКУПАЕМОСТЬ (дней копить на 1 открытие) ===");
const head = "кейс".padEnd(16) + "цена".padStart(7) +
  Object.keys(profiles).map((k) => k.split(" ")[0].padStart(10)).join("");
console.log(head);
for (const c of CASES) {
  let line = c.name.padEnd(16) + String(c.price).padStart(7);
  for (const v of Object.values(profiles)) line += (c.price / v).toFixed(1).padStart(10);
  console.log(line);
}
console.log("");

console.log("=== АНАЛИТИКА (точный RTP и шансы) ===");
for (const c of CASES) {
  const a = analytics(c);
  console.log(`${c.name}  цена=${c.price}  EV=${a.ev.toFixed(1)}  RTP=${a.rtp.toFixed(3)}`);
  console.log(`   gift=${pct(a.cats.gift || 0)} limited=${pct(a.cats.limited || 0)} ` +
    `prem3=${pct(a.cats.prem3 || 0)} prem6=${pct(a.cats.prem6 || 0)} ` +
    `jackpot=${pct(a.cats.jackpot || 0)} empty=${pct(a.cats.empty || 0)}`);
}
console.log("");

const RUNS = [10000, 100000, 1000000];
console.log("=== МОНТЕ-КАРЛО (потоки наград) ===");
const lastByCode = {}; // сохраняем 1M-прогон для пер-кодового отчёта
for (const c of CASES) {
  console.log(`\n--- ${c.name} (цена ${c.price}) ---`);
  for (const n of RUNS) {
    const s = simulate(c, n);
    if (n === 1000000) lastByCode[c.id] = s.limitedByCode;
    console.log(
      `N=${String(n).padStart(9)}  RTP=${s.rtp.toFixed(4)}  ` +
      `ср.потеря/откр=${s.netLossPerOpen.toFixed(1)}  ` +
      `Prem3=${s.counts.prem3}  Prem6=${s.counts.prem6}  ` +
      `лимиток=${s.counts.limitedItems}  джекпотов=${s.counts.jackpot}  обычн.gift=${s.counts.gift}`
    );
  }
}

console.log("\n=== РАЗДЕЛЬНАЯ СТАТИСТИКА ПО 8 ЛИМИТКАМ (1M открытий каждого кейса) ===");
const codeHead = "лимитка".padEnd(24) +
  CASES.map((c) => c.id.padStart(11)).join("");
console.log(codeHead);
for (const g of LIMITEDS) {
  let line = g.name.padEnd(24);
  for (const c of CASES) line += String(lastByCode[c.id][g.code]).padStart(11);
  console.log(line);
}


