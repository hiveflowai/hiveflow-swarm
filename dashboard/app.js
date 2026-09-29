/* Hiveflow Swarm — 16-bit pixel-art map of San Francisco.
 * Everything is drawn procedurally (no external art). Events come from a replay
 * file (dashboard/replay/*.jsonl) or live from the director (/events, SSE). */
"use strict";

const W = 400, H = 380;
const LON0 = -122.515, LON1 = -122.355, LAT0 = 37.725, LAT1 = 37.845;
const cv = document.getElementById("map");
const ctx = cv.getContext("2d");
ctx.imageSmoothingEnabled = false;

const P = { // 16-bit palette
  deep: "#1b3a6b", water: "#2456a4", water2: "#2d65bd", glint: "#8fd3ff", foam: "#d7f1ff",
  sand: "#f0dca0", land: "#8e7d6a", block1: "#c9a882", block2: "#b59474", block3: "#d8c3a0", road: "#5d5570",
  park: "#3f8f4a", park2: "#57b25d", tree: "#2a6b35", hill: "#4f7d3a", hill2: "#6a9a48",
  ink: "#f4ecd8", dim: "#9a92b8", green: "#5ee27a", amber: "#ffb627", red: "#ff4d5e", cyan: "#56e0ff",
  gold: "#ffd84d", orange: "#e8542d", black: "#0d0b1a", white: "#ffffff",
};

const px = (lon) => Math.round((lon - LON0) / (LON1 - LON0) * W);
const py = (lat) => Math.round((LAT1 - lat) / (LAT1 - LAT0) * H);
const pt = (o) => ({ x: px(o.lon), y: py(o.lat) });

// ---------------------------------------------------------------- tiny 3x5 font
const F = {
  A:"010101111101101",B:"110101110101110",C:"011100100100011",D:"110101101101110",E:"111100110100111",
  F:"111100110100100",G:"011100101101011",H:"101101111101101",I:"111010010010111",J:"001001001101010",
  K:"101101110101101",L:"100100100100111",M:"101111111101101",N:"110101101101101",O:"010101101101010",
  P:"110101110100100",Q:"010101101110011",R:"110101110101101",S:"011100010001110",T:"111010010010010",
  U:"101101101101111",V:"101101101101010",W:"101101111111101",X:"101101010101101",Y:"101101010010010",
  Z:"111001010100111","0":"111101101101111","1":"010110010010111","2":"110001010100111","3":"110001010001110",
  "4":"101101111001001","5":"111100110001110","6":"011100111101111","7":"111001010010010","8":"111101111101111",
  "9":"111101111001110"," ":"000000000000000","-":"000000111000000",".":"000000000000010",":":"000010000010000",
  "!":"010010010000010","'":"010010000000000","/":"001001010100100","?":"110001010000010","+":"000010111010000",
  "%":"101001010100101","$":"011110010011110",
};
function text(s, x, y, color = P.ink, shadow = true) {
  s = String(s).toUpperCase();
  for (let pass = shadow ? 0 : 1; pass < 2; pass++) {
    ctx.fillStyle = pass === 0 ? "#000" : color;
    let cx = x + (pass === 0 ? 1 : 0), cy = y + (pass === 0 ? 1 : 0);
    for (const ch of s) {
      const g = F[ch] || F["?"];
      for (let i = 0; i < 15; i++) if (g[i] === "1") ctx.fillRect(cx + (i % 3), cy + ((i / 3) | 0), 1, 1);
      cx += 4;
    }
  }
}
const textW = (s) => String(s).length * 4 - 1;

// ---------------------------------------------------------------- geography
const SF = [[-122.513,37.700],[-122.511,37.735],[-122.510,37.765],[-122.513,37.780],[-122.506,37.788],
  [-122.493,37.789],[-122.485,37.791],[-122.479,37.806],[-122.465,37.806],[-122.447,37.806],[-122.431,37.808],
  [-122.418,37.811],[-122.404,37.808],[-122.395,37.800],[-122.389,37.791],[-122.387,37.780],[-122.388,37.765],
  [-122.382,37.752],[-122.376,37.742],[-122.369,37.731],[-122.380,37.716],[-122.390,37.708],[-122.393,37.700]];
const MARIN = [[-122.530,37.845],[-122.530,37.829],[-122.510,37.826],[-122.492,37.829],[-122.481,37.833],
  [-122.470,37.836],[-122.455,37.842],[-122.440,37.845]];
const TREASURE = [[-122.378,37.828],[-122.366,37.830],[-122.364,37.818],[-122.374,37.816]];
const YERBA = [[-122.371,37.814],[-122.360,37.815],[-122.358,37.806],[-122.368,37.805]];
const PARKS = [
  { lon0: -122.511, lon1: -122.453, lat0: 37.7645, lat1: 37.7735 }, // Golden Gate Park
  { lon0: -122.485, lon1: -122.448, lat0: 37.788, lat1: 37.803 },   // Presidio
  { lon0: -122.437, lon1: -122.432, lat0: 37.7748, lat1: 37.7775 }, // Alamo Square
  { lon0: -122.428, lon1: -122.424, lat0: 37.7585, lat1: 37.7618 }, // Dolores Park
];
function inPoly(x, y, poly) {
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = [px(poly[i][0]), py(poly[i][1])], [xj, yj] = [px(poly[j][0]), py(poly[j][1])];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c;
  }
  return c;
}
const hash = (a, b) => { let h = (a * 374761393 + b * 668265263) | 0; h = (h ^ (h >>> 13)) * 1274126177; return ((h ^ (h >>> 16)) >>> 0) / 4294967296; };

// ---------------------------------------------------------------- static layer
const bg = document.createElement("canvas"); bg.width = W; bg.height = H;
let WORLD = null;
function buildStatic() {
  const g = bg.getContext("2d");
  const img = g.createImageData(W, H);
  const put = (x, y, hex) => { const i = (y * W + x) * 4; img.data[i] = parseInt(hex.slice(1, 3), 16);
    img.data[i + 1] = parseInt(hex.slice(3, 5), 16); img.data[i + 2] = parseInt(hex.slice(5, 7), 16); img.data[i + 3] = 255; };
  const land = new Uint8Array(W * H);
  const marketA = { x: px(-122.394), y: py(37.7955) }, marketB = { x: px(-122.435), y: py(37.763) };
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let kind = 0;
    if (inPoly(x, y, SF)) kind = 1; else if (inPoly(x, y, MARIN)) kind = 2;
    else if (inPoly(x, y, TREASURE) || inPoly(x, y, YERBA)) kind = 3;
    land[y * W + x] = kind;
    if (kind === 0) {
      const d = hash(x >> 1, y >> 1);
      put(x, y, d < 0.08 ? P.water2 : (d > 0.985 ? P.deep : P.water));
      continue;
    }
    if (kind === 2) { put(x, y, hash(x >> 2, y >> 2) < 0.5 ? P.hill : P.hill2); continue; }
    const lon = LON0 + x / W * (LON1 - LON0), lat = LAT1 - y / H * (LAT1 - LAT0);
    const park = PARKS.some((p) => lon >= p.lon0 && lon <= p.lon1 && lat >= p.lat0 && lat <= p.lat1);
    if (park) { const t = hash(x, y); put(x, y, t < 0.12 ? P.tree : (t < 0.55 ? P.park : P.park2)); continue; }
    // Market Street diagonal + street grid (rotated grid NE of Market, straight grid elsewhere)
    const dm = Math.abs((marketB.y - marketA.y) * x - (marketB.x - marketA.x) * y + marketB.x * marketA.y - marketB.y * marketA.x)
      / Math.hypot(marketB.y - marketA.y, marketB.x - marketA.x);
    const onMarket = dm < 1 && y > marketA.y - 2 && y < marketB.y + 2;
    const road = onMarket || x % 7 === 0 || y % 6 === 0;
    if (road) { put(x, y, P.road); continue; }
    const b = hash((x / 7) | 0, (y / 6) | 0), r = hash(x, y);
    const base = b < 0.33 ? P.block1 : b < 0.66 ? P.block2 : P.block3;
    const roof = (x % 7 === 1 || y % 6 === 1) ? "#e9dcc0" : ((x % 7 === 6 || y % 6 === 5) ? P.land : base);
    put(x, y, r < 0.025 ? "#7ec8e3" : roof);
  }
  // coast outline (sand) where land touches water
  for (let y = 1; y < H - 1; y++) for (let x = 1; x < W - 1; x++) {
    if (!land[y * W + x]) continue;
    if (!land[y * W + x - 1] || !land[y * W + x + 1] || !land[(y - 1) * W + x] || !land[(y + 1) * W + x]) put(x, y, P.sand);
  }
  g.putImageData(img, 0, 0);
  WORLD.land = land;

  // --- landmarks
  // Golden Gate Bridge
  const gA = { x: px(-122.4786), y: py(37.8070) }, gB = { x: px(-122.4800), y: py(37.8300) };
  g.fillStyle = P.orange; for (let y = gB.y; y <= gA.y; y++) { const t = (y - gB.y) / (gA.y - gB.y); g.fillRect(Math.round(gB.x + (gA.x - gB.x) * t), y, 2, 1); }
  for (const t of [0.22, 0.78]) { const tx = Math.round(gB.x + (gA.x - gB.x) * t), ty = Math.round(gB.y + (gA.y - gB.y) * t);
    g.fillStyle = "#b8361d"; g.fillRect(tx - 2, ty - 1, 6, 2); g.fillStyle = P.orange; g.fillRect(tx - 1, ty - 6, 1, 7); g.fillRect(tx + 2, ty - 6, 1, 7); }
  // Bay Bridge to Yerba Buena
  g.fillStyle = "#b9b4c9"; const bA = { x: px(-122.389), y: py(37.7905) }, bB = { x: px(-122.366), y: py(37.8085) };
  for (let i = 0; i <= 40; i++) { const t = i / 40; g.fillRect(Math.round(bA.x + (bB.x - bA.x) * t), Math.round(bA.y + (bB.y - bA.y) * t), 2, 1); }
  // Transamerica Pyramid
  const tr = { x: px(-122.4028), y: py(37.7952) };
  for (let i = 0; i < 14; i++) { const w = 1 + ((i / 3) | 0); g.fillStyle = i % 4 === 0 ? "#d8d4e6" : P.white; g.fillRect(tr.x - (w >> 1), tr.y - 14 + i, w, 1); }
  g.fillStyle = "#000"; g.fillRect(tr.x - 3, tr.y, 7, 1);
  // Painted Ladies
  const pl = { x: px(-122.4330), y: py(37.7762) }, cols = ["#f28fb1", "#7ec8e3", "#f7d774", "#a0e4a0", "#c59bf2"];
  cols.forEach((c, i) => { const hx = pl.x - 10 + i * 4; g.fillStyle = c; g.fillRect(hx, pl.y - 4, 4, 5);
    g.fillStyle = "#fff"; g.fillRect(hx + 1, pl.y - 6, 2, 2); g.fillRect(hx, pl.y - 5, 4, 1); g.fillStyle = "#3a2f5a"; g.fillRect(hx + 1, pl.y - 2, 1, 1); });
  // Sutro Tower
  const st = { x: px(-122.4527), y: py(37.7552) };
  for (let i = 0; i < 12; i++) { g.fillStyle = i % 3 === 0 ? P.white : P.red; g.fillRect(st.x, st.y - i, 1, 1); g.fillRect(st.x + (i < 6 ? 2 : 1), st.y - i, 1, 1); }
  g.fillStyle = P.red; g.fillRect(st.x - 1, st.y - 12, 5, 1);
  // Coit Tower
  const ct = { x: px(-122.4058), y: py(37.8024) }; g.fillStyle = "#efe6cf"; g.fillRect(ct.x, ct.y - 6, 2, 6); g.fillRect(ct.x - 1, ct.y - 7, 4, 1);

  // institutions
  for (const inst of WORLD.institutions) drawInstitution(g, inst);
  drawArena(g);
}

function drawInstitution(g, inst) {
  const { x, y } = pt(inst);
  const colors = { bank: "#e7c35a", microfinance: "#5ee27a", fintech: "#56e0ff", bureau: "#c59bf2", regulator: "#ff9f5e",
    research: "#8c1515", biobank: "#56e0ff", lab: "#b388ff", irb: "#ff9f5e" };
  if (inst.kind === "research") { // Stanford-area consortium: cardinal building with a tower
    g.fillStyle = "#000"; g.fillRect(x - 7, y - 11, 15, 13); g.fillStyle = "#f2e6d0"; g.fillRect(x - 6, y - 10, 13, 11);
    g.fillStyle = "#8c1515"; g.fillRect(x - 7, y - 12, 15, 2); g.fillRect(x - 1, y - 16, 3, 5); g.fillRect(x - 4, y - 6, 2, 3); g.fillRect(x + 3, y - 6, 2, 3);
    g.fillStyle = "#3a2f5a"; g.fillRect(x, y - 3, 1, 3); return;
  }
  if (inst.kind === "biobank" || inst.kind === "lab") { // genomics: building + DNA helix
    g.fillStyle = "#000"; g.fillRect(x - 5, y - 8, 11, 10); g.fillStyle = "#e9e2f7"; g.fillRect(x - 4, y - 7, 9, 8);
    g.fillStyle = colors[inst.kind]; g.fillRect(x - 5, y - 9, 11, 2);
    for (let i = 0; i < 6; i++) { const o = [0, 1, 2, 2, 1, 0][i]; g.fillStyle = "#ff4d5e"; g.fillRect(x - 2 + o, y - 6 + i, 1, 1); g.fillStyle = "#5b8cff"; g.fillRect(x + 2 - o, y - 6 + i, 1, 1); }
    return;
  }
  g.fillStyle = "#000"; g.fillRect(x - 5, y - 8, 11, 10);
  g.fillStyle = "#e9e2f7"; g.fillRect(x - 4, y - 7, 9, 8);
  g.fillStyle = colors[inst.kind] || P.gold; g.fillRect(x - 5, y - 9, 11, 2);
  if (inst.kind === "bank" || inst.kind === "regulator") { g.fillStyle = "#b8b0d4"; for (let i = -3; i <= 3; i += 2) g.fillRect(x + i, y - 6, 1, 6); }
  else { g.fillStyle = "#6f8bd6"; g.fillRect(x - 3, y - 5, 2, 2); g.fillRect(x + 1, y - 5, 2, 2); g.fillRect(x - 3, y - 2, 2, 2); g.fillRect(x + 1, y - 2, 2, 2); }
}
function drawArena(g) {
  const { x, y } = pt(WORLD.arena);
  g.fillStyle = "#000"; g.fillRect(x - 6, y - 8, 13, 10);
  g.fillStyle = "#4a1020"; g.fillRect(x - 5, y - 7, 11, 8);
  g.fillStyle = P.red; g.fillRect(x - 6, y - 9, 13, 2); g.fillRect(x - 1, y - 5, 3, 3);
}

// ---------------------------------------------------------------- sprites
const SKIN = ["#ffdbac", "#f1c27d", "#e0ac69", "#c68642", "#8d5524"];
const HAIR = ["#2b1b0e", "#5a3825", "#d6b370", "#111111", "#a0522d", "#8e8e8e", "#c0392b"];
const SHIRT = ["#5b8cff", "#ff7aa2", "#ffd166", "#06d6a0", "#b388ff", "#ff9f1c", "#4ecdc4", "#ef476f"];
function strHash(s) { let h = 2166136261; for (const c of s) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }

function drawChibi(x, y, look, frame, opts = {}) {
  x = Math.round(x); y = Math.round(y);
  const { skin, hair, shirt } = look;
  const P2 = (c, dx, dy, w = 1, h = 1) => { ctx.fillStyle = c; ctx.fillRect(x + dx, y + dy, w, h); };
  P2("rgba(0,0,0,.35)", -2, 5, 5, 1); // shadow
  // legs
  P2("#2b2540", -2, 3, 2, 2 - (frame ? 1 : 0)); P2("#2b2540", 1, 3, 2, 2 - (frame ? 0 : 1));
  // body
  P2(opts.hood || shirt, -3, 0, 7, 3); P2(skin, -4, 1, 1, 1); P2(skin, 4, 1, 1, 1);
  // head (big, chibi)
  P2("#000", -3, -7, 7, 7); P2(skin, -2, -6, 5, 5);
  P2(opts.hood || hair, -3, -8, 7, 3); if (!opts.hood) P2(hair, -3, -6, 1, 2);
  P2("#000", -1, -4, 1, 1); P2("#000", 1, -4, 1, 1);
  if (opts.hood) { P2(opts.hood, -3, -6, 1, 4); P2(opts.hood, 3, -6, 1, 4); P2(P.red, -1, -4, 1, 1); P2(P.red, 1, -4, 1, 1); }
  return { x, y };
}

function bubble(x, y, ch, color, bg = P.white) {
  x = Math.round(x); y = Math.round(y);
  ctx.fillStyle = "#000"; ctx.fillRect(x - 3, y - 18, 7, 8); ctx.fillStyle = bg; ctx.fillRect(x - 2, y - 17, 5, 6);
  ctx.fillRect(x, y - 11, 1, 1); text(ch, x - 1, y - 16, color, false);
}

// ---------------------------------------------------------------- scene state
const S = {
  dels: [], scammers: [], couriers: [], fx: [], pulses: [], rings: [], nodes: {}, hoodShield: {},
  counters: { leaks: 0, harm: 0, fooled: 0, screened: 0, imp: 0, ask: 0, commit: 0 },
  round: 0, mode: "federated", model: "-", vaccines: {}, cards: {}, perRound: { federated: {}, isolated: {} },
};
const HUB = () => ({ x: px(-122.432), y: py(37.8285) });

function setupWorld() {
  S.dels = WORLD.delegates.map((d) => {
    const hood = WORLD.neighborhoods.find((h) => h.id === d.hood);
    const c = pt(hood), h = strHash(d.id);
    const home = { x: c.x + d.x * 20, y: c.y + d.y * 14 };
    return { ...d, home, pos: { ...home }, target: { ...home }, state: "green", committed: false, frame: 0,
      look: { skin: SKIN[h % SKIN.length], hair: HAIR[(h >>> 3) % HAIR.length], shirt: SHIRT[(h >>> 6) % SHIRT.length] },
      speed: 5 + (h % 5), flash: 0 };
  });
  for (const h of WORLD.neighborhoods) {
    const words = h.label.split(" "); const short = h.label.length <= 16 ? h.label : words.slice(0, 2).join(" ");
    S.nodes[h.id] = { ...pt(h), label: h.label, short, online: false, blink: 0 };
  }
}

function hoodCenter(id) { const h = WORLD.neighborhoods.find((x) => x.id === id); return h ? pt(h) : HUB(); }
function instPos(id) { const i = WORLD.institutions.find((x) => x.id === id); return i ? pt(i) : pt(WORLD.arena); }

function float(textStr, x, y, color, life = 2.2) { S.fx.push({ kind: "text", text: textStr, x, y, color, t: 0, life }); }
function banner(msg, ms = 2600) {
  const b = document.getElementById("banner"); b.textContent = msg; b.style.display = "block";
  clearTimeout(banner._t); banner._t = setTimeout(() => (b.style.display = "none"), ms / SPEED);
}

// ---------------------------------------------------------------- event handling
const QUEUE = [];
let SPEED = 1, PLAYING = true, waitUntil = 0, SOURCE = "replay", REPLAY = { federated: [], isolated: [] };
let SCENARIO = "health";
const COPY = {
  health: { sub: "patients' delegates in San Francisco hospitals · genome and records never leave the hospital",
            insight: "WHAT THE STUDY LEARNS (AGGREGATES ONLY)", commit: "Patient-signed consents" },
  finance: { sub: "people's delegates in San Francisco neighborhoods · banks and a microlender",
             insight: "WHAT THE LENDER LEARNS (AGGREGATES ONLY)", commit: "Human-approved commitments" },
};

const HANDLERS = {
  "swarm.round_start"(e) {
    S.round = e.round; S.mode = e.mode; S.model = e.coordinator_model;
    setText("round", e.round); setText("mode", e.mode.toUpperCase()); setText("model", String(e.coordinator_model).toUpperCase());
    for (const d of S.dels) { if (d.state !== "shield") d.state = "green"; }
    banner(`ROUND ${e.round}`); return 1.6;
  },
  "swarm.nodes"(e) {
    for (const n of e.nodes) for (const h of n.hoods) if (S.nodes[h]) { S.nodes[h].online = true; S.nodes[h].flwr = n.name; }
    return 0.4;
  },
  "swarm.offer"(e) {
    const to = hoodCenter(e.hood);
    if (e.attack) {
      const from = pt(WORLD.arena);
      S.scammers.push({ offer: e.offer, hood: e.hood, sender: e.sender, pos: { ...from }, target: { x: to.x + 6, y: to.y - 4 },
        phase: "go", t: 0, look: { skin: "#c9a27e", hair: "#000", shirt: "#000" } });
    } else {
      const from = instPos(e.sender);
      S.couriers.push({ pos: { ...from }, target: { ...to }, t: 0 });
    }
    return 0.12;
  },
  "swarm.assigned"() { for (const id in S.nodes) S.pulses.push({ from: HUB(), to: S.nodes[id], t: 0, color: P.gold }); return 2.4; },
  "swarm.hood_result"(e) {
    const node = S.nodes[e.hood]; if (node) S.pulses.push({ from: node, to: HUB(), t: 0, color: P.cyan });
    const st = e.stats;
    S.counters.fooled += st.fooled; S.counters.screened += st.screened; S.counters.imp += st.identity_rejected;
    S.counters.s1 = (S.counters.s1 || 0) + (st.system1_blocked || 0);
    S.counters.ask += st.awaiting_human;
    for (const dd of e.delegates) {
      const d = S.dels.find((x) => x.id === dd.id); if (!d) continue;
      d.state = dd.state; if (dd.state === "red") d.flash = 1.2;
      if (dd.committed) { d.committed = true; S.counters.commit++; float("SIGNED", d.pos.x - 11, d.pos.y - 16, P.gold); }
    }
    for (const sc of S.scammers.filter((s) => s.hood === e.hood && s.phase !== "gone")) {
      if (e.identity_rejected.some((r) => r.offer === sc.offer)) { sc.phase = "rejected"; sc.t = 0; float("NO ID", sc.pos.x - 9, sc.pos.y - 16, P.red); }
      else if (st.fooled > 0) { sc.phase = "blocked"; sc.t = 0; float("BLOCKED", sc.pos.x - 13, sc.pos.y - 18, P.red); }
      else { sc.phase = "shielded"; sc.t = 0; float("VACCINE", sc.pos.x - 13, sc.pos.y - 18, P.cyan); }
    }
    if (e.vaccines_active && e.vaccines_active.length) S.hoodShield[e.hood] = e.vaccines_active.length;
    updateCounters(); return 0.5;
  },
  "swarm.insight"(e) { renderInsight(e); return 0.4; },
  "swarm.vaccine_candidate"(e) {
    S.vaccines[e.pattern.id] = { ...e.pattern, origin: e.origin_hood };
    addCard(`vax-${e.pattern.id}`, `VACCINE from ${e.origin_hood}: block ${e.pattern.kind} “${e.pattern.indicators.join(", ")}”`, "vax",
      { kind: "vaccine", id: `vax-${e.pattern.id}`, by: e.decided_by });
    const c = hoodCenter(e.origin_hood); float("NEW PATTERN", c.x - 21, c.y - 26, P.cyan); return 0.8;
  },
  "swarm.approval_needed"(e) {
    const a = e.request.action;
    addCard(e.request.id, `${nameOf(a.delegate)} (${a.hood}) · ${a.title} · ${a.scope}`, "ask", { kind: "commitment", id: e.request.id });
    return 0.08;
  },
  "swarm.round_complete"(e) {
    S.perRound[e.mode][e.round] = e.stats.fooled; drawChart();
    S.counters.leaks = e.stats.personal_data_leaks; S.counters.harm = e.stats.harmful_executed; updateCounters();
    feedAudit(e.node_audits || []);
    banner(`ROUND ${e.round} COMPLETE · ${e.stats.fooled} FOOLED · 0 EXECUTED · 0 DATA MOVED`, 2600);
    return 2.4;
  },
  "swarm.coordinator_audit"(e) { feedAudit([{ node: "coordinator", entries: e.entries.slice(-8) }]); return 0.2; },
  "swarm.human_decision"(e) { resolveCard(e.request, e.approved); return 0.1; },
  "swarm.vaccine_approved"(e) {
    resolveCard(`vax-${e.pattern}`, true);
    const v = S.vaccines[e.pattern] || { origin: "mission", indicators: e.indicators };
    const c = hoodCenter(v.origin);
    S.rings.push({ x: c.x, y: c.y, r: 2, t: 0, mode: S.mode, origin: v.origin });
    banner(`VACCINE ADOPTED${S.mode === "federated" ? " BY THE FEDERATION" : " (ONLY " + String(v.origin).toUpperCase() + ")"}: ${e.indicators.join(", ")}`, 3000);
    return 1.6;
  },
  "swarm.timeout"(e) { banner(`TIMEOUT: ${e.pending.length} NODES LATE`); return 1; },
  "swarm.reply_rejected"(e) { float("REJECTED", S.nodes[Object.keys(S.nodes)[0]].x, 40, P.red); return 0.3; },
  "director.run_started"(e) { banner(`FLOWER RUN ${e.run_id} STARTED`); return 0.5; },
  "director.run_failed"() { banner("RUN FAILED — SEE DIRECTOR LOG"); return 1; },
};

function nameOf(id) { const d = S.dels.find((x) => x.id === id); return d ? d.name : id; }
function setText(id, v) { document.getElementById(id).textContent = v; }
function updateCounters() {
  const c = S.counters;
  setText("c-leaks", c.leaks); setText("c-harm", c.harm); setText("c-fooled", c.fooled); setText("c-screened", c.screened);
  setText("c-imp", c.imp); setText("c-s1", c.s1 || 0); setText("c-ask", Object.values(S.cards).filter((x) => !x.done && x.cls === "ask").length); setText("c-commit", c.commit);
}

// approvals ------------------------------------------------------------------
function addCard(id, label, cls, meta) {
  const box = document.getElementById("approvals");
  const ph = document.getElementById("no-approvals"); if (ph) ph.remove();
  const el = document.createElement("div"); el.className = `card ${cls === "vax" ? "vax" : ""}`; el.id = `card-${id}`;
  el.innerHTML = `<div>${cls === "vax" ? "🛡" : "❗"} ${escapeHtml(label)}</div>` + (SOURCE === "live"
    ? `<div class="row"><button class="ok">APPROVE</button><button class="no">DENY</button></div>` : `<div class="row muted">waiting for a human…</div>`);
  if (SOURCE === "live") {
    el.querySelector(".ok").onclick = () => decide(meta, true); el.querySelector(".no").onclick = () => decide(meta, false);
  }
  box.prepend(el); S.cards[id] = { el, done: false, cls }; updateCounters();
  while (box.children.length > 30) box.lastChild.remove();
}
function resolveCard(id, approved) {
  const c = S.cards[id]; if (!c || c.done) return; c.done = true; c.el.classList.add("done");
  const row = c.el.querySelector(".row"); if (row) row.innerHTML = approved ? "✔ approved · signed by a human" : "✖ denied";
  updateCounters();
}
async function decide(meta, approved) {
  try {
    await fetch("/api/decide", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ ...meta, approved }) });
    resolveCard(meta.id, approved);
  } catch (err) { banner("DIRECTOR NOT REACHABLE"); }
}
const escapeHtml = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// audit ------------------------------------------------------------------------
const AUDIT = [];
function feedAudit(nodeAudits) {
  const items = [];
  for (const a of nodeAudits) for (const e of a.entries) items.push({ ...e, node: a.node });
  items.forEach((e, i) => setTimeout(() => {
    const d = e.data || {};
    const what = d.rule || d.result || d.code || d.type || (d.patterns ? "vaccines " + d.patterns.length : "") || "";
    AUDIT.unshift(`<span class="h">${(e.hash || "").slice(0, 8)}</span> ${escapeHtml(e.actor)} ${escapeHtml(e.type)} ${escapeHtml(what)} <span style="color:var(--green)">✓</span>`);
    AUDIT.length = Math.min(AUDIT.length, 24);
    document.getElementById("audit").innerHTML = AUDIT.map((l) => `<div>${l}</div>`).join("");
  }, (i * 90) / SPEED));
}

// insight ------------------------------------------------------------------------
function renderInsight(e) {
  const tot = Object.values(e.stances).reduce((a, b) => a + b, 0) || 1;
  const colors = { interested: "var(--green)", neutral: "var(--dim)", not_interested: "var(--amber)", suspicious: "var(--red)" };
  const bars = Object.entries(e.stances).sort((a, b) => b[1] - a[1]).map(([k, v]) =>
    `<div><span style="width:120px;display:inline-block">${k.replace("_", " ")}</span><i style="width:${Math.round(v / tot * 180)}px;background:${colors[k] || "var(--cyan)"}"></i>${v}</div>`).join("");
  const ins = e.insight || {};
  document.getElementById("insight").innerHTML = `<div style="color:var(--ink)">${escapeHtml(ins.headline || "")}</div>
    <div class="bars" style="margin:6px 0">${bars}</div>
    <div class="muted">${(ins.insights || []).slice(0, 3).map(escapeHtml).join(" · ")}</div>
    <div class="muted" style="font-size:15px">decided_by: ${escapeHtml(ins.decided_by || "-")} · no individual record left any node</div>`;
}

// chart --------------------------------------------------------------------------
function drawChart() {
  const c = document.getElementById("chart"), g = c.getContext("2d"); g.imageSmoothingEnabled = false;
  g.fillStyle = "#0d0b1a"; g.fillRect(0, 0, c.width, c.height);
  const rounds = [1, 2, 3, 4, 5], max = Math.max(4, ...rounds.flatMap((r) => [S.perRound.federated[r] || 0, S.perRound.isolated[r] || 0]));
  const bw = 18, gap = 16, base = c.height - 22;
  g.fillStyle = "#3a3366"; g.fillRect(10, base, c.width - 20, 2);
  rounds.forEach((r, i) => {
    const x = 20 + i * (bw * 2 + gap);
    for (const [k, col, off] of [["federated", "#56e0ff", 0], ["isolated", "#ff4d5e", bw]]) {
      const v = S.perRound[k][r]; if (v === undefined) continue;
      const h = Math.round(v / max * (base - 14));
      g.fillStyle = "#000"; g.fillRect(x + off + 2, base - h + 2, bw - 2, h);
      g.fillStyle = col; g.fillRect(x + off, base - h, bw - 2, h);
      g.fillStyle = "#f4ecd8"; g.font = "16px VT323, monospace"; g.fillText(String(v), x + off + 4, base - h - 3);
    }
    g.fillStyle = "#9a92b8"; g.font = "16px VT323, monospace"; g.fillText(`R${r}`, x + bw - 6, c.height - 5);
  });
}

// ---------------------------------------------------------------- simulation loop
let last = performance.now(), clock = 0;
function tick(dt) {
  clock += dt;
  if (PLAYING && QUEUE.length && clock >= waitUntil) {
    const e = QUEUE.shift(); const h = HANDLERS[e.type]; const delay = h ? h(e) : 0; waitUntil = clock + (delay || 0);
  }
  update(dt);
}
function step(now) {
  const dt = Math.min(0.05, (now - last) / 1000) * SPEED; last = now;
  tick(dt); render(); requestAnimationFrame(step);
}

function moveTo(o, target, speed, dt) {
  const dx = target.x - o.pos.x, dy = target.y - o.pos.y, d = Math.hypot(dx, dy);
  if (d < 0.5) return true; const s = Math.min(d, speed * dt); o.pos.x += dx / d * s; o.pos.y += dy / d * s; return false;
}

function update(dt) {
  for (const d of S.dels) {
    if (moveTo(d, d.target, d.speed, dt) || Math.random() < 0.002) {
      const a = Math.random() * Math.PI * 2, r = Math.random() * 9;
      d.target = { x: d.home.x + Math.cos(a) * r, y: d.home.y + Math.sin(a) * r * 0.7 };
    }
    d.frame = (clock * 4 + d.speed) % 2 < 1 ? 0 : 1; d.flash = Math.max(0, d.flash - dt);
  }
  for (const s of S.scammers) {
    s.t += dt;
    if (s.phase === "go") moveTo(s, s.target, 26, dt);
    else if (s.phase === "rejected" || s.phase === "shielded") { const back = pt(WORLD.arena); moveTo(s, back, 34, dt); if (s.t > 3) s.phase = "gone"; }
    else if (s.phase === "blocked") { if (s.t > 2.2) s.phase = "gone"; }
  }
  S.scammers = S.scammers.filter((s) => s.phase !== "gone");
  for (const c of S.couriers) { c.t += dt; if (moveTo(c, c.target, 40, dt)) c.done = true; }
  S.couriers = S.couriers.filter((c) => !c.done);
  for (const p of S.pulses) p.t += dt * 0.9; S.pulses = S.pulses.filter((p) => p.t < 1);
  for (const f of S.fx) { f.t += dt; f.y -= dt * 6; } S.fx = S.fx.filter((f) => f.t < f.life);
  for (const r of S.rings) {
    r.t += dt; r.r += dt * 70;
    for (const [id, n] of Object.entries(S.nodes)) {
      const inReach = Math.hypot(n.x - r.x, n.y - r.y) < r.r;
      if (inReach && (r.mode === "federated" || id === r.origin)) { S.hoodShield[id] = (S.hoodShield[id] || 0) || 1;
        for (const d of S.dels) if (d.hood === id && d.state === "green") d.state = "shield"; }
    }
  }
  S.rings = S.rings.filter((r) => r.t < 4);
}

function render() {
  ctx.drawImage(bg, 0, 0);
  // water glints
  for (let i = 0; i < 26; i++) {
    const x = (hash(i, 7) * W + clock * 6 * (i % 2 ? 1 : -1)) % W, y = hash(i, 3) * H;
    if (!WORLD.land[(y | 0) * W + ((x | 0) + W) % W] && Math.sin(clock * 3 + i) > 0.6) { ctx.fillStyle = P.glint; ctx.fillRect(x | 0, y | 0, 2, 1); }
  }
  // SuperLink hub and links
  const hub = HUB(); const blink = Math.sin(clock * 4) > 0;
  ctx.fillStyle = "rgba(255,216,77,.25)";
  for (const n of Object.values(S.nodes)) if (n.online) dotted(hub, n);
  drawHub(hub, blink);
  // pulses
  for (const p of S.pulses) { const x = p.from.x + (p.to.x - p.from.x) * p.t, y = p.from.y + (p.to.y - p.from.y) * p.t;
    ctx.fillStyle = "#000"; ctx.fillRect(x - 1, y - 1, 4, 4); ctx.fillStyle = p.color; ctx.fillRect(x, y, 2, 2); }
  // vaccine rings
  for (const r of S.rings) {
    ring(r.x, r.y, r.r, r.mode === "federated" ? P.cyan : "#7aa7b8", 1 - r.t / 4);
    if (SCENARIO === "health") for (let k = 0; k < 10; k++) { // Y-shaped antibodies riding the wave
      const a = k / 10 * Math.PI * 2 + r.t, ax = Math.round(r.x + Math.cos(a) * r.r), ay = Math.round(r.y + Math.sin(a) * r.r * 0.75);
      ctx.globalAlpha = 1 - r.t / 4; ctx.fillStyle = "#ffd84d"; ctx.fillRect(ax, ay, 1, 3); ctx.fillRect(ax - 1, ay - 1, 1, 1); ctx.fillRect(ax + 1, ay - 1, 1, 1);
      ctx.fillRect(ax - 2, ay - 2, 1, 1); ctx.fillRect(ax + 2, ay - 2, 1, 1); ctx.globalAlpha = 1;
    }
  }
  // node shields
  for (const [id, n] of Object.entries(S.nodes)) {
    if (S.hoodShield[id]) ring(n.x, n.y, 25 + Math.sin(clock * 2) * 1.5, P.cyan, 0.35);
  }
  // SuperNode markers
  for (const [id, n] of Object.entries(S.nodes)) drawNode(n, id);
  // institutions labels
  for (const inst of WORLD.institutions) { const p = pt(inst); const s = inst.tag || inst.label; text(s, p.x - (textW(s) >> 1), p.y + 4, P.gold); }
  const ar = pt(WORLD.arena); text("ARENA", ar.x - 9, ar.y + 4, P.white);
  // couriers (legit offers)
  for (const c of S.couriers) { ctx.fillStyle = "#000"; ctx.fillRect(c.pos.x - 3, c.pos.y - 3, 7, 6); ctx.fillStyle = P.white; ctx.fillRect(c.pos.x - 2, c.pos.y - 2, 5, 4);
    ctx.fillStyle = P.green; ctx.fillRect(c.pos.x - 2, c.pos.y - 2, 5, 1); }
  // sprites sorted by y
  const sprites = [...S.dels.map((d) => ({ kind: "d", y: d.pos.y, d })), ...S.scammers.map((s) => ({ kind: "s", y: s.pos.y, s }))].sort((a, b) => a.y - b.y);
  for (const sp of sprites) {
    if (sp.kind === "d") {
      const d = sp.d; const shake = d.flash > 0 ? Math.sin(clock * 60) : 0;
      drawChibi(d.pos.x + shake, d.pos.y, d.look, d.frame);
      if (d.state === "amber") bubble(d.pos.x, d.pos.y, "!", "#b35c00", P.gold);
      else if (d.state === "red") bubble(d.pos.x, d.pos.y, "X", P.white, P.red);
      else if (d.state === "shield") { ctx.globalAlpha = 0.5 + 0.3 * Math.sin(clock * 5 + d.speed); ring(d.pos.x, d.pos.y - 3, 7, P.cyan, 1); ctx.globalAlpha = 1; }
      if (d.committed && Math.sin(clock * 6 + d.speed) > 0.7) { ctx.fillStyle = P.gold; ctx.fillRect(d.pos.x + 4, d.pos.y - 10, 1, 1); }
    } else {
      const s = sp.s; ctx.globalAlpha = s.phase === "rejected" || s.phase === "shielded" ? Math.max(0, 1 - s.t / 3) : 1;
      drawChibi(s.pos.x, s.pos.y, s.look, ((clock * 6) | 0) % 2, { hood: "#b21e35" });
      if (s.phase === "rejected") { ctx.fillStyle = P.red; for (let i = -4; i <= 4; i++) { ctx.fillRect(s.pos.x + i, s.pos.y - 4 + i, 1, 1); ctx.fillRect(s.pos.x + i, s.pos.y - 4 - i, 1, 1); } }
      ctx.globalAlpha = 1;
    }
  }
  // floating texts
  for (const f of S.fx) { ctx.globalAlpha = Math.max(0, 1 - f.t / f.life); text(f.text, Math.round(f.x), Math.round(f.y), f.color); ctx.globalAlpha = 1; }
  // title plate
  text("SAN FRANCISCO", 6, H - 10, P.gold);
}

function dotted(a, b) { const n = Math.hypot(b.x - a.x, b.y - a.y) / 4; ctx.fillStyle = "rgba(255,216,77,.35)";
  for (let i = 0; i < n; i++) { const t = i / n; if (((i + ((clock * 8) | 0)) % 3) === 0) ctx.fillRect(Math.round(a.x + (b.x - a.x) * t), Math.round(a.y + (b.y - a.y) * t), 1, 1); } }
function ring(x, y, r, color, alpha = 1) { ctx.globalAlpha = alpha; ctx.fillStyle = color;
  const n = Math.max(12, (r * 6) | 0); for (let i = 0; i < n; i++) { const a = i / n * Math.PI * 2; ctx.fillRect(Math.round(x + Math.cos(a) * r), Math.round(y + Math.sin(a) * r * 0.75), 1, 1); } ctx.globalAlpha = 1; }
function drawHub(h, blink) {
  ctx.fillStyle = "#000"; ctx.fillRect(h.x - 6, h.y - 6, 13, 13);
  const petals = [[0, -4], [4, 0], [0, 4], [-4, 0]]; ctx.fillStyle = P.gold;
  for (const [dx, dy] of petals) ctx.fillRect(h.x + dx - 2, h.y + dy - 2, 5, 5);
  ctx.fillStyle = blink ? P.white : P.orange; ctx.fillRect(h.x - 1, h.y - 1, 3, 3);
  text("SUPERLINK", h.x - 17, h.y - 13, P.gold);
}
function drawNode(n, id) {
  const x = n.x, y = n.y + 14;
  if (SCENARIO === "health") { // hospital: white block with a red cross
    ctx.fillStyle = "#000"; ctx.fillRect(x - 6, y - 3, 13, 10); ctx.fillStyle = "#f4f1ea"; ctx.fillRect(x - 5, y - 2, 11, 8);
    ctx.fillStyle = "#e63946"; ctx.fillRect(x - 1, y - 1, 3, 7); ctx.fillRect(x - 3, y + 1, 7, 3);
    ctx.fillStyle = n.online && Math.sin(clock * 6 + x) > 0 ? P.green : "#1a4d2a"; ctx.fillRect(x + 4, y - 2, 1, 1);
    text(n.short, x - (textW(n.short) >> 1), y + 9, n.online ? P.ink : P.dim); return;
  }
  ctx.fillStyle = "#000"; ctx.fillRect(x - 4, y - 1, 9, 7); ctx.fillStyle = n.online ? "#2b2760" : "#333"; ctx.fillRect(x - 3, y, 7, 5);
  ctx.fillStyle = n.online && Math.sin(clock * 6 + x) > 0 ? P.green : "#1a4d2a"; ctx.fillRect(x - 2, y + 1, 1, 1); ctx.fillRect(x - 2, y + 3, 1, 1);
  text(n.short, x - (textW(n.short) >> 1), y + 7, n.online ? P.ink : P.dim);
}

// ---------------------------------------------------------------- data sources
async function loadReplay(mode) {
  const txt = await (await fetch(`replay/${SCENARIO}-${mode}.jsonl`)).text();
  return txt.split("\n").filter(Boolean).map((l) => JSON.parse(l));
}
function resetScene(mode) {
  QUEUE.length = 0; S.scammers = []; S.couriers = []; S.fx = []; S.pulses = []; S.rings = []; S.hoodShield = {};
  S.counters = { leaks: 0, harm: 0, fooled: 0, screened: 0, imp: 0, ask: 0, commit: 0 }; S.cards = {}; S.vaccines = {};
  AUDIT.length = 0; document.getElementById("audit").innerHTML = ""; document.getElementById("approvals").innerHTML = '<div id="no-approvals" class="muted">none yet</div>';
  S.perRound[mode] = {}; setupWorld(); updateCounters(); drawChart(); waitUntil = clock;
}
function playReplay(mode) {
  SOURCE = "replay"; setText("src", "REPLAY"); resetScene(mode);
  // keep the other mode's bars for comparison
  const other = mode === "federated" ? "isolated" : "federated";
  for (const e of REPLAY[other]) if (e.type === "swarm.round_complete") S.perRound[other][e.round] = e.stats.fooled;
  QUEUE.push(...REPLAY[mode]); drawChart();
}
function goLive() {
  SOURCE = "live"; setText("src", "● LIVE"); resetScene(S.mode);
  for (const k of ["federated", "isolated"]) for (const e of REPLAY[k]) if (e.type === "swarm.round_complete") S.perRound[k][e.round] = e.stats.fooled;
  S.perRound.live = {}; drawChart();
  document.getElementById("next").style.display = document.getElementById("approve-all").style.display = "inline-block";
  fetch("/api/info").then((r) => r.json()).then(async (info) => {
    if (info.scenario && info.scenario !== SCENARIO) { await loadScenario(info.scenario); }
    document.getElementById("qr-panel").style.display = "block";
    document.getElementById("qr-url").textContent = info.phone_url + ` · backend: ${info.backend} ${info.backend === "flower" ? info.connection : ""}`;
    if (window.QRCode) new QRCode(document.getElementById("qr"), { text: info.phone_url, width: 96, height: 96 });
  }).catch(() => {});
  const es = new EventSource("/events");
  es.onmessage = (m) => { try { QUEUE.push(JSON.parse(m.data)); } catch (err) { /* ignore */ } };
  es.onerror = () => banner("LIVE STREAM DISCONNECTED");
}

// ---------------------------------------------------------------- controls & tooltip
document.getElementById("play").onclick = (e) => { PLAYING = !PLAYING; e.target.textContent = PLAYING ? "▶ PLAY" : "⏸ PAUSED"; e.target.classList.toggle("on", PLAYING); };
document.getElementById("speed").onclick = (e) => { SPEED = SPEED === 1 ? 2 : SPEED === 2 ? 4 : 1; e.target.textContent = `${SPEED}X`; };
document.getElementById("restart").onclick = () => playReplay(S.mode);
document.getElementById("m-fed").onclick = () => { S.mode = "federated"; toggleMode(); playReplay("federated"); };
document.getElementById("m-iso").onclick = () => { S.mode = "isolated"; toggleMode(); playReplay("isolated"); };
document.getElementById("live").onclick = () => { location.search = "?live=1"; };
document.getElementById("next").onclick = async () => {
  const r = await (await fetch("/api/round", { method: "POST", body: "{}" })).json();
  if (!r.ok) banner(String(r.error || "COULD NOT START ROUND").toUpperCase());
};
document.getElementById("approve-all").onclick = () => fetch("/api/decide_all", { method: "POST", body: "{}" });
function toggleMode() { document.getElementById("m-fed").classList.toggle("on", S.mode === "federated"); document.getElementById("m-iso").classList.toggle("on", S.mode === "isolated"); }

cv.addEventListener("mousemove", (ev) => {
  const r = cv.getBoundingClientRect(), x = (ev.clientX - r.left) / r.width * W, y = (ev.clientY - r.top) / r.height * H;
  const d = S.dels.find((q) => Math.abs(q.pos.x - x) < 4 && Math.abs(q.pos.y - 3 - y) < 6);
  const tip = document.getElementById("tip");
  if (!d) { tip.style.display = "none"; return; }
  tip.style.display = "block"; tip.style.left = `${ev.clientX - r.left + 14}px`; tip.style.top = `${ev.clientY - r.top + 10}px`;
  const mandate = SCENARIO === "health"
    ? "read offers, reply, pre-screen for trials, pay trusted providers · never share the genome"
    : "read offers, reply, pre-apply, pay trusted payees";
  tip.innerHTML = `<b>${escapeHtml(d.name)}'s delegate</b> (${WORLD.person})<br>${escapeHtml(d.summary)}<br>` +
    `<span class="muted">mandate: ${mandate} · every commitment needs ${escapeHtml(d.name)}'s signature</span><br>` +
    `state: <b>${d.state}</b>${d.committed ? " · signed commitment" : ""}<br><span class="muted">${SCENARIO === "health" ? "genome and records stay" : "private data stays"} on node sf-${d.hood}</span>`;
});

async function loadScenario(sc) {
  SCENARIO = sc;
  WORLD = await (await fetch(`world-${sc}.json`)).json();
  const g = bg.getContext("2d"); g.clearRect(0, 0, W, H); buildStatic(); setupWorld();
  [REPLAY.federated, REPLAY.isolated] = await Promise.all([loadReplay("federated"), loadReplay("isolated")]);
  S.perRound = { federated: {}, isolated: {} };
  document.getElementById("subtitle").textContent = COPY[sc].sub;
  document.getElementById("insight-title").textContent = COPY[sc].insight;
  document.getElementById("commit-label").textContent = COPY[sc].commit;
  document.getElementById("s-health").classList.toggle("on", sc === "health");
  document.getElementById("s-finance").classList.toggle("on", sc === "finance");
}
document.getElementById("s-health").onclick = async () => { await loadScenario("health"); playReplay(S.mode); };
document.getElementById("s-finance").onclick = async () => { await loadScenario("finance"); playReplay(S.mode); };

(async function main() {
  const qs = new URLSearchParams(location.search);
  await loadScenario(qs.get("scenario") === "finance" ? "finance" : "health");
  if (qs.get("live") === "1") goLive(); else playReplay(qs.get("mode") === "isolated" ? "isolated" : "federated");
  const ff = Number(qs.get("ff") || 0); // fast-forward N seconds (for screenshots / jumping in a demo)
  for (let t = 0; t < ff; t += 0.05) tick(0.05);
  requestAnimationFrame(step);
})();
