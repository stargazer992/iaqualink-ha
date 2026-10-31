/*
 * iaqualink-pool-light-card.js  v2.0.0
 * Pool & Spa light picker for the iCL light entities of the iaqualink custom integration
 * (https://github.com/stargazer992/iaqualink-ha). Based on fleur-pool-light-card v1.2.0, same look and layout.
 * The integration serves and loads this file itself: no Lovelace resource is needed.
 *
 * Card YAML:
 *   type: custom:iaqualink-pool-light-card
 *   pool_entity: light.pool      # optional, these are the defaults
 *   spa_entity: light.spa
 *
 * Calls light.turn_on / light.turn_off (brightness_pct, rgb_color) and homeassistant.update_entity (Sync).
 * Presets are sent as RGB colours. Custom colours are remembered in this browser only (localStorage).
 * The "Stays on" timer in the power pop-up still needs fleur-popup.js and an auto-off automation that knows the light entity.
 * There is NO access token in this file. Every call is wrapped: an error shows a message and the card keeps working.
 */

// ---------------------------------------------------------------- variables
const FPLC_VERSION = "2.0.0";
const FPLC_ZONES = ["pool", "spa"];                       // lights
const FPLC_ZONE_LABEL = { pool: "Pool", spa: "Spa", both: "Both" };
const FPLC_DEFAULT_RGB = { r: 27, g: 205, b: 255 };       // turquoise, used when a state is unknown
const FPLC_DEFAULT_BRIGHTNESS = 50;
const FPLC_PRESETS = [                                    // the PyScript preset names are the lower-case names
  { name: "Turquoise", r: 27, g: 205, b: 255 },
  { name: "White", r: 255, g: 255, b: 255 },
  { name: "Blue", r: 0, g: 100, b: 255 },
  { name: "Green", r: 0, g: 255, b: 100 },
];
const FPLC_CUSTOM_SLOTS = 4;
const FPLC_STORE_KEY = "poolLightCustomColors";           // same key as the old page, so saved colours carry over
const FPLC_WHEEL_SIZE = 156;                              // px
const FPLC_OPTIMISTIC_MS = 8000;                          // show the sent value this long, until the light reports back
const FPLC_HOLD_MS = 500;                                 // press and hold a custom slot to select it for saving
const FPLC_MSG_MS = 2600;                                 // how long a status message stays
const FPLC_LOG = "[iaqualink-pool-light-card]";               // prefix of every console message
const FPLC_POWER_HOLD_MS = 550;                           // press and hold a Pool / Spa power tile for the On / Off / Stays on pop-up
const FPLC_POP_WIDTH = 440;                               // px, width of that pop-up
const FPLC_ZONE_ICON = { pool: "mdi:pool", spa: "mdi:hot-tub" };
const FPLC_DEFAULT_ENTITY = { pool: "light.pool", spa: "light.spa" };

const FPLC_STYLE = `
:host { display:block; }
* { box-sizing:border-box; }
.card { --accent:#C9A66B; --gold-hi:#E6CB96; --gold-lo:#A47F47; --txt:#F4EFE7; --mut:#B9B3A9; --dim:#7d776e; --edge:rgba(255,255,255,.15); --hair:rgba(255,255,255,.08); --bad:#FF9C92; --on-accent:#1b1a18;
  font-family:Jost, 'Century Gothic', 'Avenir Next', -apple-system, 'SF Pro Display', sans-serif; font-weight:400; font-size:17px; line-height:1.3; color:var(--txt); padding:6px 0 4px; user-select:none; -webkit-user-select:none; }
button { font-family:inherit; color:inherit; cursor:pointer; -webkit-tap-highlight-color:transparent; touch-action:manipulation; }
.cap { font-size:13px; letter-spacing:.3em; text-transform:uppercase; color:var(--mut); }

/* zone selector */
.zones { display:flex; border-bottom:1px solid var(--hair); }
.zones button { flex:1; background:none; border:none; padding:14px 4px 15px; font-size:15px; font-weight:500; letter-spacing:.24em; text-transform:uppercase; color:var(--mut); position:relative; transition:color .2s; }
.zones button.on { color:var(--gold-hi); }
.zones button.on::after { content:""; position:absolute; left:18%; right:18%; bottom:-1px; height:2px; background:var(--accent); border-radius:2px; }

/* power tiles */
.pw { display:grid; grid-template-columns:1fr 1fr; gap:12px; padding:18px 0 6px; }
.pt { display:flex; align-items:center; gap:14px; padding:12px 14px; border:1px solid var(--edge); border-radius:18px; background:rgba(255,255,255,.04); text-align:left; transition:border-color .2s, background .2s; }
.pt.on { border-color:rgba(201,166,107,.7); background:linear-gradient(90deg, rgba(201,166,107,.16), rgba(201,166,107,.04)); }
.pt .sw { flex:none; width:40px; height:40px; border-radius:50%; display:flex; align-items:center; justify-content:center; background:#2d2d2e; border:1px solid var(--edge); transition:background .3s, box-shadow .3s; }
.pt .sw svg { width:20px; height:20px; stroke:var(--dim); fill:none; stroke-width:2; stroke-linecap:round; }
.pt.on .sw svg { stroke:#fff; filter:drop-shadow(0 0 2px rgba(0,0,0,.6)); }
.pt .tx { min-width:0; }
.pt .nm { font-size:15px; letter-spacing:.2em; text-transform:uppercase; font-weight:500; }
.pt .st { font-size:14px; color:var(--dim); margin-top:2px; letter-spacing:.08em; }
.pt.on .st { color:var(--gold-hi); }

/* colour section */
.cs { display:flex; gap:20px; align-items:center; padding:18px 0 8px; flex-wrap:wrap; }
.wheel { flex:none; width:${FPLC_WHEEL_SIZE}px; height:${FPLC_WHEEL_SIZE}px; margin:0 auto; touch-action:none; cursor:crosshair; border-radius:50%; box-shadow:0 0 0 1px rgba(201,166,107,.55), 0 8px 24px rgba(0,0,0,.5); }
.ctl { flex:1 1 190px; min-width:0; display:flex; flex-direction:column; gap:14px; }
.chip { height:44px; border-radius:14px; border:1px solid var(--edge); display:flex; align-items:center; justify-content:center; font-size:14px; letter-spacing:.18em; text-transform:uppercase; font-weight:500; transition:background-color .15s; }
.br .row { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:8px; }
.br .v { font-size:18px; font-weight:300; color:var(--gold-hi); font-variant-numeric:tabular-nums; }
input[type=range] { -webkit-appearance:none; appearance:none; width:100%; height:6px; border-radius:3px; outline:none; margin:6px 0; cursor:pointer;
  background:linear-gradient(90deg, var(--gold-lo), var(--gold-hi)) 0/var(--p,50%) 100% no-repeat, rgba(255,255,255,.18); }
input[type=range]::-webkit-slider-thumb { -webkit-appearance:none; width:24px; height:24px; border-radius:50%; background:#F4EFE7; border:3px solid rgba(201,166,107,.7); box-shadow:0 2px 8px rgba(0,0,0,.5); }
input[type=range]::-moz-range-thumb { width:18px; height:18px; border-radius:50%; background:#F4EFE7; border:3px solid rgba(201,166,107,.7); box-shadow:0 2px 8px rgba(0,0,0,.5); }
.btns { display:flex; gap:10px; }
.btn { flex:1; padding:12px 10px; border-radius:999px; border:1px solid var(--edge); background:transparent; font-size:14px; letter-spacing:.22em; text-transform:uppercase; font-weight:500; transition:transform .15s, background .2s, border-color .2s; }
.btn:active { transform:scale(.97); }
.btn.go { background:linear-gradient(180deg, var(--gold-hi), var(--accent)); border-color:transparent; color:var(--on-accent); font-weight:600; }
.btn:hover:not(.go) { border-color:rgba(201,166,107,.6); }
.btn.hide { display:none; }

/* presets */
.sec { margin-top:18px; padding-top:16px; border-top:1px solid var(--hair); }
.sec .cap { margin-bottom:12px; display:flex; justify-content:space-between; }
.sec .cap i { font-style:normal; letter-spacing:.12em; color:var(--dim); text-transform:none; font-size:13px; }
.grid { display:grid; grid-template-columns:repeat(4, 1fr); gap:12px 8px; }
.pr { background:none; border:none; padding:0; display:flex; flex-direction:column; align-items:center; gap:7px; }
.pr .dot { width:46px; height:46px; border-radius:50%; border:1px solid var(--edge); box-shadow:0 0 0 0 transparent; transition:transform .15s, box-shadow .2s; display:flex; align-items:center; justify-content:center; color:var(--dim); font-size:22px; font-weight:300; }
.pr:active .dot { transform:scale(.92); }
.pr .lb { font-size:12px; letter-spacing:.16em; text-transform:uppercase; color:var(--mut); }
.pr.empty .dot { border-style:dashed; background:rgba(255,255,255,.03); }
.pr.sel .dot { box-shadow:0 0 0 3px rgba(201,166,107,.85); }
.pr.sel .lb { color:var(--gold-hi); }

.foot { display:flex; flex-wrap:wrap; gap:10px; margin-top:18px; }
.foot .btn { flex:1 1 150px; font-size:13px; }
.msg { min-height:22px; margin-top:12px; text-align:center; font-size:14px; letter-spacing:.14em; color:var(--gold-hi); opacity:0; transition:opacity .25s; }
.msg.show { opacity:1; } .msg.err { color:var(--bad); }
`;

const fplcPower = '<svg viewBox="0 0 24 24"><path d="M12 2v10M18.4 6.6a9 9 0 1 1-12.8 0"/></svg>';

// ---------------------------------------------------------------- colour maths
const fplcHsvToRgb = (h, s, v) => {
  const c = v * s, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = v - c;
  let r = 0, g = 0, b = 0;
  if (h < 60) { r = c; g = x; } else if (h < 120) { r = x; g = c; } else if (h < 180) { g = c; b = x; }
  else if (h < 240) { g = x; b = c; } else if (h < 300) { r = x; b = c; } else { r = c; b = x; }
  return { r: Math.round((r + m) * 255), g: Math.round((g + m) * 255), b: Math.round((b + m) * 255) };
};
const fplcRgbToHsv = (r, g, b) => {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
  let h = 0;
  if (d !== 0) {
    if (max === r) h = ((g - b) / d) % 6; else if (max === g) h = (b - r) / d + 2; else h = (r - g) / d + 4;
    h *= 60; if (h < 0) h += 360;
  }
  return { h, s: max === 0 ? 0 : d / max, v: max };
};
const fplcIsLight = (c) => (c.r + c.g + c.b) > 400;
const fplcCss = (c) => "rgb(" + c.r + "," + c.g + "," + c.b + ")";
const fplcEl = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt !== undefined) e.textContent = txt; return e; };

class IaqualinkPoolLightCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._built = false;
    this._zone = "pool";                                    // pool | spa | both
    this._ui = Object.assign({}, FPLC_DEFAULT_RGB, { br: FPLC_DEFAULT_BRIGHTNESS });
    this._slot = null;                                      // custom slot selected for saving
    this._opt = {};                                         // optimistic values per zone
    this._sig = "";
    this._dragging = false;
    this._msgTimer = null;
  }
  setConfig(cfg) { this._cfg = cfg || {}; if (!this._built) this._build(); }
  getCardSize() { return 8; }
  set hass(h) { this._hass = h; if (this._built) this._sync(); }

  // ---------------------------------------------------------------- state
  _num(id, dflt) {
    try { const s = this._hass && this._hass.states[id]; const n = s ? parseFloat(s.state) : NaN; return isNaN(n) ? dflt : n; } catch (e) { return dflt; }
  }
  _ent(zone) { return (this._cfg && this._cfg[zone + "_entity"]) || FPLC_DEFAULT_ENTITY[zone]; }
  _light(zone) {
    let s = null;
    try { s = this._hass && this._hass.states[this._ent(zone)]; } catch (e) { /* ignore */ }
    const a = (s && s.attributes) || {};
    const rgb = Array.isArray(a.rgb_color) ? a.rgb_color : null;
    const l = {
      power: !!s && s.state === "on",
      br: typeof a.brightness === "number" ? Math.round(a.brightness * 100 / 255) : FPLC_DEFAULT_BRIGHTNESS,
      r: rgb ? rgb[0] : FPLC_DEFAULT_RGB.r,
      g: rgb ? rgb[1] : FPLC_DEFAULT_RGB.g,
      b: rgb ? rgb[2] : FPLC_DEFAULT_RGB.b,
    };
    const o = this._opt[zone];
    if (o && o.until > Date.now()) Object.assign(l, o.v);
    return l;
  }
  _setOpt(zone, v) { this._opt[zone] = { until: Date.now() + FPLC_OPTIMISTIC_MS, v: Object.assign((this._opt[zone] && this._opt[zone].until > Date.now()) ? this._opt[zone].v : {}, v) }; }
  _targets() { return this._zone === "both" ? FPLC_ZONES.slice() : [this._zone]; }

  // ---------------------------------------------------------------- services
  // the card's actions, sent as light / homeassistant service calls
  _native(action, d) {
    const entity_id = d.zone ? this._ent(d.zone) : FPLC_ZONES.map((z) => this._ent(z));
    switch (action) {
      case "power": return ["light", d.power === "on" ? "turn_on" : "turn_off", { entity_id }];
      case "brightness": return ["light", "turn_on", { entity_id, brightness_pct: Math.max(1, Math.min(100, Math.round(d.value))) }];
      case "rgb": return ["light", "turn_on", { entity_id, rgb_color: [d.red, d.green, d.blue] }];
      case "preset": {
        const p = FPLC_PRESETS.find((x) => x.name.toLowerCase() === d.preset) || FPLC_PRESETS[0];
        return ["light", "turn_on", { entity_id, rgb_color: [p.r, p.g, p.b] }];
      }
      case "sync": return ["homeassistant", "update_entity", { entity_id }];
      default: throw new Error("unknown action " + action);
    }
  }
  async _call(action, data) {
    try {
      if (!this._hass) throw new Error("not connected");
      const [domain, service, payload] = this._native(action, data);
      await this._hass.callService(domain, service, payload);
      return true;
    } catch (err) {
      console.error(FPLC_LOG, action, err);
      this._say("Could not reach the light (" + action + ")", true);
      return false;
    }
  }
  _say(text, bad) {
    try {
      const m = this._m;
      if (!m) return;
      m.textContent = text; m.classList.toggle("err", !!bad); m.classList.add("show");
      clearTimeout(this._msgTimer);
      this._msgTimer = setTimeout(() => m.classList.remove("show"), FPLC_MSG_MS);
    } catch (e) { /* ignore */ }
  }

  // ---------------------------------------------------------------- build
  _build() {
    try {
      this._built = true;
      const root = this.shadowRoot;
      root.innerHTML = "";
      const st = document.createElement("style"); st.textContent = FPLC_STYLE; root.append(st);
      const card = fplcEl("div", "card"); root.append(card);

      // zone selector
      const zones = fplcEl("div", "zones"); this._zoneBtns = {};
      for (const z of ["pool", "spa", "both"]) {
        const b = fplcEl("button", "", FPLC_ZONE_LABEL[z]); b.addEventListener("click", () => this._setZone(z));
        zones.append(b); this._zoneBtns[z] = b;
      }
      card.append(zones);

      // power tiles
      const pw = fplcEl("div", "pw"); this._pt = {};
      for (const z of FPLC_ZONES) {
        const t = fplcEl("button", "pt"); const sw = fplcEl("span", "sw"); sw.innerHTML = fplcPower;
        const tx = fplcEl("span", "tx"); const nm = fplcEl("div", "nm", FPLC_ZONE_LABEL[z]); const s = fplcEl("div", "st", "Off");
        tx.append(nm, s); t.append(sw, tx);
        // tap = switch the light, hold = pop-up (On / Off / Stays on); a hold never also fires the tap
        let ptimer = null, pheld = false;
        const pdown = () => { pheld = false; clearTimeout(ptimer); ptimer = setTimeout(() => { pheld = true; ptimer = null; this._powerPopup(z); }, FPLC_POWER_HOLD_MS); };
        const pup = () => { if (ptimer) { clearTimeout(ptimer); ptimer = null; } };
        t.addEventListener("pointerdown", pdown);
        ["pointerup", "pointerleave", "pointercancel"].forEach((ev) => t.addEventListener(ev, pup));
        t.addEventListener("contextmenu", (e) => e.preventDefault());
        t.addEventListener("click", () => { if (pheld) { pheld = false; return; } this._togglePower(z); });
        pw.append(t); this._pt[z] = { t, sw, s };
      }
      card.append(pw);

      // colour wheel + controls
      const cs = fplcEl("div", "cs");
      const wheel = fplcEl("canvas", "wheel"); this._wheel = wheel; cs.append(wheel);
      const ctl = fplcEl("div", "ctl");
      this._chip = fplcEl("div", "chip", ""); ctl.append(this._chip);
      const br = fplcEl("div", "br"); const row = fplcEl("div", "row"); row.append(fplcEl("span", "cap", "Brightness"));
      this._brV = fplcEl("span", "v", "50%"); row.append(this._brV);
      this._brS = document.createElement("input"); this._brS.type = "range"; this._brS.min = "0"; this._brS.max = "100"; this._brS.value = "50";
      this._brS.addEventListener("input", () => { this._ui.br = parseInt(this._brS.value, 10) || 0; this._paintBr(); });
      this._brS.addEventListener("change", () => this._setBrightness());
      br.append(row, this._brS); ctl.append(br);
      const btns = fplcEl("div", "btns");
      this._apply = fplcEl("button", "btn go", "Apply"); this._apply.addEventListener("click", () => this._applyColor());
      const save = fplcEl("button", "btn", "Save"); save.addEventListener("click", () => this._saveCustom());
      this._saveBtn = save;
      btns.append(this._apply, save); ctl.append(btns);
      this._syncBtns();
      cs.append(ctl); card.append(cs);
      this._initWheel();

      // presets
      const sec = fplcEl("div", "sec");
      sec.append(fplcEl("div", "cap", "Presets"));
      this._presetGrid = fplcEl("div", "grid"); sec.append(this._presetGrid);
      const cap2 = fplcEl("div", "cap"); cap2.style.marginTop = "18px"; cap2.append(document.createTextNode("Custom"), fplcEl("i", "", "tap to use · hold to pick a slot, then Save"));
      sec.append(cap2);
      this._customGrid = fplcEl("div", "grid"); sec.append(this._customGrid);
      card.append(sec);
      this._buildPresets();

      // footer
      const foot = fplcEl("div", "foot");
      this._copy = fplcEl("button", "btn", "Copy to Spa"); this._copy.addEventListener("click", () => this._copyToOther());
      const sync = fplcEl("button", "btn", "Sync from iAqualink"); sync.addEventListener("click", () => this._syncFromApp());
      foot.append(this._copy, sync); card.append(foot);
      this._m = fplcEl("div", "msg", ""); card.append(this._m);

      this._setZone(this._zone, true);
      if (this._hass) this._sync();
    } catch (err) { console.error(FPLC_LOG + " build", err); }
  }

  _buildPresets() {
    try {
      this._presetGrid.innerHTML = ""; this._customGrid.innerHTML = "";
      for (const p of FPLC_PRESETS) {
        const b = fplcEl("button", "pr"); const d = fplcEl("span", "dot"); d.style.background = fplcCss(p);
        b.append(d, fplcEl("span", "lb", p.name)); b.addEventListener("click", () => this._applyPreset(p, false));
        this._presetGrid.append(b);
      }
      const saved = this._loadCustom();
      for (let i = 0; i < FPLC_CUSTOM_SLOTS; i++) {
        const c = saved[i];
        const b = fplcEl("button", "pr" + (c ? "" : " empty") + (this._slot === i ? " sel" : ""));
        const d = fplcEl("span", "dot"); if (c) d.style.background = fplcCss(c); else d.textContent = "+";
        b.append(d, fplcEl("span", "lb", "Custom " + (i + 1)));
        // tap = use (or select when empty); hold or right-click = select this slot for saving
        let timer = null, held = false;
        const down = () => { held = false; timer = setTimeout(() => { held = true; this._selectSlot(i); }, FPLC_HOLD_MS); };
        const up = () => { clearTimeout(timer); };
        b.addEventListener("pointerdown", down); b.addEventListener("pointerup", up); b.addEventListener("pointerleave", up); b.addEventListener("pointercancel", up);
        b.addEventListener("contextmenu", (e) => { e.preventDefault(); });
        b.addEventListener("click", () => { if (held) { held = false; return; } if (c) this._applyPreset(c, true); else this._selectSlot(i); });
        this._customGrid.append(b);
      }
    } catch (err) { console.error(FPLC_LOG + " presets", err); }
  }

  // ---------------------------------------------------------------- colour wheel
  _initWheel() {
    const c = this._wheel;
    const dpr = window.devicePixelRatio || 1, w = Math.round(FPLC_WHEEL_SIZE * dpr);
    c.width = w; c.height = w;
    const ctx = c.getContext("2d");
    const img = ctx.createImageData(w, w), R = w / 2 - 1, cx = w / 2, cy = w / 2;
    for (let y = 0; y < w; y++) for (let x = 0; x < w; x++) {
      const dx = x + .5 - cx, dy = y + .5 - cy, d = Math.sqrt(dx * dx + dy * dy), i = (y * w + x) * 4;
      if (d > R) { img.data[i + 3] = 0; continue; }
      let a = Math.atan2(dy, dx) * 180 / Math.PI; if (a < 0) a += 360;
      const rgb = fplcHsvToRgb(a, Math.min(d / R, 1), 1);
      img.data[i] = rgb.r; img.data[i + 1] = rgb.g; img.data[i + 2] = rgb.b; img.data[i + 3] = d > R - 1.2 ? Math.round(255 * (R - d) / 1.2) : 255;
    }
    this._wheelImg = img; this._wheelDpr = dpr;
    const pick = (e) => {
      const r = c.getBoundingClientRect(), x = (e.clientX - r.left) / r.width * w - cx, y = (e.clientY - r.top) / r.height * w - cy;
      const d = Math.sqrt(x * x + y * y);
      let a = Math.atan2(y, x) * 180 / Math.PI; if (a < 0) a += 360;
      const rgb = fplcHsvToRgb(a, Math.min(d / R, 1), 1);
      this._ui.r = rgb.r; this._ui.g = rgb.g; this._ui.b = rgb.b;
      this._paintColor();
    };
    c.addEventListener("pointerdown", (e) => { try { this._dragging = true; c.setPointerCapture(e.pointerId); pick(e); } catch (err) { console.error(FPLC_LOG + " wheel", err); } });
    c.addEventListener("pointermove", (e) => { if (this._dragging) pick(e); });
    const end = () => { this._dragging = false; };
    c.addEventListener("pointerup", end); c.addEventListener("pointercancel", end);
  }
  _drawWheel() {
    try {
      const c = this._wheel, ctx = c.getContext("2d"), w = c.width, R = w / 2 - 1;
      ctx.putImageData(this._wheelImg, 0, 0);
      const hsv = fplcRgbToHsv(this._ui.r, this._ui.g, this._ui.b), a = hsv.h * Math.PI / 180;
      const x = w / 2 + hsv.s * R * Math.cos(a), y = w / 2 + hsv.s * R * Math.sin(a), k = this._wheelDpr;
      ctx.beginPath(); ctx.arc(x, y, 9 * k, 0, 2 * Math.PI); ctx.fillStyle = fplcCss(this._ui); ctx.fill();
      ctx.lineWidth = 2.5 * k; ctx.strokeStyle = "#F4EFE7"; ctx.stroke();
      ctx.beginPath(); ctx.arc(x, y, 10.8 * k, 0, 2 * Math.PI); ctx.lineWidth = 1 * k; ctx.strokeStyle = "rgba(0,0,0,.55)"; ctx.stroke();
    } catch (err) { console.error(FPLC_LOG + " wheel", err); }
  }

  // ---------------------------------------------------------------- paint
  _paintColor() {
    const u = this._ui, css = fplcCss(u);
    this._chip.style.backgroundColor = css; this._chip.style.color = fplcIsLight(u) ? "#161617" : "#F4EFE7";
    this._chip.textContent = "R " + u.r + " · G " + u.g + " · B " + u.b;
    this._drawWheel();
    this._syncBtns();
  }
  // Rule 3: Apply only shows when the picked colour differs from the light; Save only shows while a Custom slot is selected
  _syncBtns() {
    try {
      if (!this._apply || !this._saveBtn) return;
      const u = this._ui; let diff = false;
      for (const z of this._targets()) { const l = this._light(z); if (l.r !== u.r || l.g !== u.g || l.b !== u.b) diff = true; }
      this._apply.style.display = diff ? "" : "none";
      this._saveBtn.style.display = this._slot !== null ? "" : "none";
    } catch (err) { console.error(FPLC_LOG + " buttons", err); }
  }
  _paintBr() {
    this._brV.textContent = this._ui.br + "%";
    this._brS.value = String(this._ui.br);
    this._brS.style.setProperty("--p", this._ui.br + "%");
  }
  _paintPower() {
    this._syncBtns();
    for (const z of FPLC_ZONES) {
      const l = this._light(z), p = this._pt[z];
      p.t.classList.toggle("on", l.power);
      if (l.power) { p.sw.style.background = fplcCss(l); p.sw.style.boxShadow = "0 0 16px rgba(" + l.r + "," + l.g + "," + l.b + ",.55)"; p.s.textContent = "On · " + l.br + "%"; }
      else { p.sw.style.background = ""; p.sw.style.boxShadow = "none"; p.s.textContent = "Off"; }
    }
  }
  // the colour of the zone being edited follows the light, unless the user is busy with the wheel
  _sync() {
    try {
      this._paintPower();
      const src = this._light(this._zone === "both" ? "pool" : this._zone);
      const sig = [src.r, src.g, src.b, src.br].join(",");
      if (sig !== this._sig && !this._dragging) { this._sig = sig; this._loadUi(src); }
    } catch (err) { console.error(FPLC_LOG + " sync", err); }
  }
  _loadUi(l) {
    this._ui.r = l.r; this._ui.g = l.g; this._ui.b = l.b; this._ui.br = l.br;
    this._paintColor(); this._paintBr();
  }

  // ---------------------------------------------------------------- actions
  _setZone(z, quiet) {
    this._zone = z;
    for (const k of Object.keys(this._zoneBtns)) this._zoneBtns[k].classList.toggle("on", k === z);
    this._copy.classList.toggle("hide", z === "both");
    if (z !== "both") this._copy.textContent = "Copy to " + FPLC_ZONE_LABEL[z === "pool" ? "spa" : "pool"];
    this._sig = ""; this._paintPower();
    this._loadUi(this._light(z === "both" ? "pool" : z));
    if (!quiet) this._sig = "";
  }
  async _togglePower(z) {
    const on = !this._light(z).power;
    this._setOpt(z, { power: on }); this._paintPower();
    if (await this._call("power", { zone: z, power: on ? "on" : "off" })) this._say(FPLC_ZONE_LABEL[z] + (on ? " light on" : " light off"));
  }
  // hold on a power tile: On / Off + Stays on (timed off) in the shared pop-up kit
  _powerPopup(z) {
    try {
      const FP = window.FleurPopup;
      if (!FP) { console.error(FPLC_LOG + " fleur-popup.js is not loaded"); return null; }
      const entity = this._ent(z);
      const hassNow = () => this._hass || {};
      const m = { on: this._light(z).power };
      const m0 = { on: m.on };                      // what the light is doing now (updated after Apply)
      let ch = null, ao = null;
      return FP.open({
        title: FPLC_ZONE_LABEL[z] + " Light", icon: FPLC_ZONE_ICON[z] || "mdi:lightbulb", width: FPLC_POP_WIDTH,
        sub: () => "Now: " + (this._light(z).power ? "on \u00b7 " + this._light(z).br + "%" : "off"),
        build: (d) => {
          const sec = d.section("Power");
          ch = d.chips(sec, { options: [{ value: 0, label: "Off" }, { value: 1, label: "On" }], value: m.on ? 1 : 0, onChange: (v) => { m.on = v === 1; } });
          ao = d.autoOff(null, { hass: hassNow, entity: entity, onChange: () => {
            // picking a timer while it is off means "turn it on for that long": switch it on in the pop-up (Apply sends it)
            try { if (ao && !m.on && ao.get() > 0) { m.on = true; ch.set(1); } } catch (err) { console.error(FPLC_LOG + " auto-off pick", err); }
            d.changed();
          } });
        },
        snapshot: () => ({ on: m.on, a: m.on && ao ? ao.get() : 0 }),
        apply: async (d) => {
          let ok = true;
          if (m.on && ao && !ao.valid()) { d.msg("Enter the number of minutes (1-1440).", true); return false; }
          if (m.on !== m0.on) {
            this._setOpt(z, { power: m.on }); this._paintPower();
            ok = (await this._call("power", { zone: z, power: m.on ? "on" : "off" })) && ok;
          }
          try {
            if (ao) {
              if (m.on) await ao.apply();
              else if (((hassNow().services || {}).pyscript || {}).auto_off_clear) await hassNow().callService("pyscript", "auto_off_clear", { entity_id: entity });
            }
          } catch (err) { console.error(FPLC_LOG + " auto-off", entity, err); ok = false; }
          if (ok) { m0.on = m.on; d.msg("Saved \u2713", false); } else d.msg("Could not save everything - try again", true);
          return ok;
        },
      });
    } catch (err) { console.error(FPLC_LOG + " power pop-up", err); return null; }
  }
  async _setBrightness() {
    const v = this._ui.br;
    // an off light keeps the level for later: light.turn_on would light it
    for (const z of this._targets()) { const on = this._light(z).power; this._setOpt(z, { br: v }); if (on) await this._call("brightness", { zone: z, value: v }); }
    this._paintPower();
  }
  async _applyColor() {
    const u = this._ui, t = this._targets();
    for (const z of t) this._setOpt(z, { r: u.r, g: u.g, b: u.b, power: true });
    this._paintPower();
    let ok = true;
    for (const z of t) ok = (await this._call("rgb", { zone: z, red: u.r, green: u.g, blue: u.b })) && ok;
    if (ok) this._say("Applied");
  }
  async _applyPreset(p, custom) {
    this._ui.r = p.r; this._ui.g = p.g; this._ui.b = p.b; this._paintColor();
    const t = this._targets();
    for (const z of t) this._setOpt(z, { r: p.r, g: p.g, b: p.b, power: true });
    this._paintPower();
    let ok = true;
    for (const z of t) {
      ok = (custom
        ? await this._call("rgb", { zone: z, red: p.r, green: p.g, blue: p.b })
        : await this._call("preset", { zone: z, preset: p.name.toLowerCase() })) && ok;
    }
    if (ok) this._say(p.name + " applied");
  }
  async _copyToOther() {
    const from = this._zone, to = from === "pool" ? "spa" : "pool";
    const l = this._light(from);
    this._setOpt(to, { r: l.r, g: l.g, b: l.b, power: true });
    this._setZone(to);
    if (await this._call("rgb", { zone: to, red: l.r, green: l.g, blue: l.b })) this._say("Copied to " + FPLC_ZONE_LABEL[to]);
  }
  async _syncFromApp() {
    if (await this._call("sync", {})) this._say("Synced from iAqualink");
  }

  // ---------------------------------------------------------------- custom colours
  _loadCustom() {
    try {
      const raw = window.localStorage.getItem(FPLC_STORE_KEY), arr = raw ? JSON.parse(raw) : [];
      const out = [];
      for (let i = 0; i < FPLC_CUSTOM_SLOTS; i++) { const c = arr[i]; out.push(c && typeof c.r === "number" ? { name: "Custom " + (i + 1), r: c.r, g: c.g, b: c.b } : null); }
      return out;
    } catch (e) { return new Array(FPLC_CUSTOM_SLOTS).fill(null); }
  }
  _storeCustom(list) { try { window.localStorage.setItem(FPLC_STORE_KEY, JSON.stringify(list)); return true; } catch (e) { return false; } }
  _selectSlot(i) { this._slot = this._slot === i ? null : i; this._buildPresets(); this._syncBtns(); if (this._slot !== null) this._say("Custom " + (this._slot + 1) + " selected: pick a colour, then Save"); }
  _saveCustom() {
    if (this._slot === null) { this._say("Hold a Custom slot first", true); return; }
    const list = this._loadCustom();
    list[this._slot] = { name: "Custom " + (this._slot + 1), r: this._ui.r, g: this._ui.g, b: this._ui.b };
    const n = this._slot + 1;
    if (this._storeCustom(list)) this._say("Saved to Custom " + n); else this._say("Could not save in this browser", true);
    this._slot = null; this._buildPresets(); this._syncBtns();
  }
}
if (!customElements.get("iaqualink-pool-light-card")) customElements.define("iaqualink-pool-light-card", IaqualinkPoolLightCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "iaqualink-pool-light-card", name: "iAquaLink Pool & Spa Light", description: "Colour, brightness and power for iAquaLink iCL pool and spa lights." });
console.info("%c IAQUALINK-POOL-LIGHT %c v" + FPLC_VERSION + " ", "background:#C9A66B;color:#1b1a18", "");
