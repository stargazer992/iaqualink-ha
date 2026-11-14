/*
 * jandy-bridge-card.js  v1.2.0
 * The Jandy Bridge's own salt + RS8 card (the bridge page in its ?embed view), inside Home Assistant.
 * The integration serves and loads this file; the page and its API go through
 * /api/iaqualink/bridge/ with the HA login, so it works wherever HA does and needs no bridge password.
 *
 * The bridge page runs in a sandboxed iframe with an opaque origin: it can't reach HA, its login
 * or its storage. Its fetches to the bridge API are posted to this card, which sends only
 * bridge pass-through requests (GETs, and the POSTs the pass-through allows) with the HA login.
 *
 * Card YAML:
 *   type: custom:jandy-bridge-card
 *   dark: true        # optional: start in the dark faceplate in a browser that hasn't picked one
 *
 * Display settings and presses in progress come from the bridge, so the card and the bridge page match;
 * the card reloads the page by itself when the bridge's page changes.
 */
const JBC_BASE = "/api/iaqualink/bridge/";
const JBC_RETRY_MS = 30000;
const JBC_CHECK_MS = 60000;
const JBC_POSTS = new Set(["api/cmd", "api/ha/link/request", "api/ui", "api/ui/press"]);
const JBC_MAX_BODY = 65536;
const JBC_STORE_KEY = /^jb[A-Za-z_][\w-]{0,40}$/;

// Runs first inside the sandboxed page. B: the pass-through URL, O: HA's origin, L: the page's saved settings.
function jbcBoot(B, O, L) {
  window.JB_EMBED = "rs8";
  const post = (m) => parent.postMessage(m, O), wait = new Map();
  let n = 0;
  const st = new Map(Object.entries(L)), api = {
    getItem: (k) => (st.has(String(k)) ? st.get(String(k)) : null),
    setItem: (k, v) => { st.set(String(k), String(v)); post({ jb: "ls", k: String(k), v: String(v) }); },
    removeItem: (k) => { st.delete(String(k)); post({ jb: "ls", k: String(k), v: null }); },
    clear: () => { for (const k of [...st.keys()]) api.removeItem(k); },
    key: (i) => [...st.keys()][i] ?? null,
  };
  const ls = new Proxy(api, {
    get: (t, k) => (k === "length" ? st.size : k in t ? t[k] : api.getItem(k)),
    set: (t, k, v) => { api.setItem(k, v); return true; },
    deleteProperty: (t, k) => { api.removeItem(k); return true; },
  });
  Object.defineProperty(window, "localStorage", { value: ls, configurable: true });
  const nf = window.fetch.bind(window);
  window.fetch = (u, i) => {
    const x = new URL(typeof u === "string" ? u : u.url, B);
    if (x.origin !== O || !x.pathname.startsWith(new URL(B).pathname)) return nf(u, i);
    i = i || {};
    const h = new Headers(i.headers || {});
    if (i.body != null && typeof i.body !== "string") return Promise.reject(new TypeError("Jandy Bridge card: only text bodies"));
    return new Promise((ok, no) => {
      const id = ++n;
      wait.set(id, { ok, no });
      post({ jb: "fetch", id, path: x.pathname + x.search, method: (i.method || "GET").toUpperCase(), type: h.get("Content-Type") || "", body: i.body ?? null });
    });
  };
  addEventListener("message", (e) => {
    const d = e.data;
    if (e.source !== parent || e.origin !== O || !d || d.jb !== "res" || !wait.has(d.id)) return;
    const w = wait.get(d.id);
    wait.delete(d.id);
    if (d.error) w.no(new TypeError(d.error));
    else w.ok(new Response([101, 204, 205, 304].includes(d.status) ? null : d.body, { status: d.status, headers: { "Content-Type": d.type || "text/plain" } }));
  });
  let lh = 0;
  setInterval(() => {
    const c = document.getElementById("c-rs8");
    if (!c) return;
    const pop = document.querySelector("#rs8pop>.card");
    const h = Math.ceil(Math.max(c.getBoundingClientRect().bottom + scrollY + 2, pop ? pop.scrollHeight / 0.94 + 24 : 0));
    if (Math.abs(h - lh) > 1) post({ jb: "h", h: (lh = h) });
  }, 300);
}

class JandyBridgeCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._started) {
      this._started = true;
      this._load();
    }
  }

  getCardSize() {
    return 8;
  }

  getGridOptions() {
    return { columns: "full", min_columns: 6 };
  }

  connectedCallback() {
    if (!this._onmsg) {
      this._onmsg = (e) => this._message_in(e);
      window.addEventListener("message", this._onmsg);
    }
    if (this._frame && !this._check) {
      this._check = setInterval(() => this._changed(), JBC_CHECK_MS);
      this._changed();
    }
    else if (this._started && !this._frame && !this._retry) this._load();
  }

  disconnectedCallback() {
    window.removeEventListener("message", this._onmsg);
    this._onmsg = null;
    clearTimeout(this._retry);
    this._retry = null;
    clearInterval(this._check);
    this._check = null;
  }

  async _changed() {
    try {
      const r = await this._hass.fetchWithAuth(JBC_BASE);
      if (r.ok && (await r.text()) !== this._html) this._load();
    } catch (e) {
      /* bridge away for a moment: keep the page, the next check retries */
    }
  }

  _message(text) {
    this.replaceChildren();
    this._frame = null;
    const d = document.createElement("div");
    d.style.cssText = "padding:16px;border-radius:10px;background:var(--card-background-color);color:var(--secondary-text-color)";
    d.textContent = text;
    this.append(d);
  }

  async _load() {
    let html;
    try {
      const r = await this._hass.fetchWithAuth(JBC_BASE);
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "HTTP " + r.status);
      html = await r.text();
    } catch (e) {
      this._message("Jandy Bridge not reachable (" + e.message + "). Trying again in 30 s.");
      if (this.isConnected) this._retry = setTimeout(() => { this._retry = null; this._load(); }, JBC_RETRY_MS);
      return;
    }
    this._html = html;
    if (this._config.dark && localStorage.getItem("jb_rs8dark") === null) localStorage.setItem("jb_rs8dark", "1");
    const base = new URL(JBC_BASE, location.href).href;
    const saved = {};
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (JBC_STORE_KEY.test(k)) saved[k] = localStorage.getItem(k);
    }
    const arg = (v) => JSON.stringify(v).replace(/</g, "\\u003c");
    const boot =
      '<base href="' + base.replace(/"/g, "&quot;") + '"><script>(' + jbcBoot.toString() + ")(" +
      arg(base) + "," + arg(location.origin) + "," + arg(saved) + ")<\/script>";
    html = /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, (m) => m + boot) : boot + html;
    const f = document.createElement("iframe");
    f.title = "Jandy Bridge";
    f.setAttribute("sandbox", "allow-scripts allow-modals");
    f.style.cssText = "border:0;width:100%;height:420px;display:block;background:transparent;color-scheme:light";
    f.srcdoc = html;
    this.replaceChildren(f);
    this._frame = f;
    clearInterval(this._check);
    this._check = setInterval(() => this._changed(), JBC_CHECK_MS);
  }

  // Requests from the sandboxed bridge page: only its own frame, only the bridge pass-through.
  async _message_in(e) {
    const f = this._frame, d = e.data;
    if (!f || e.source !== f.contentWindow || e.origin !== "null" || !d || typeof d !== "object") return;
    if (d.jb === "h") {
      const h = Math.min(Math.max(Math.round(+d.h) || 0, 60), 4000);
      if (Math.abs(f.offsetHeight - h) > 1) f.style.height = h + "px";
    } else if (d.jb === "ls") {
      if (typeof d.k !== "string" || !JBC_STORE_KEY.test(d.k)) return;
      if (d.v === null) localStorage.removeItem(d.k);
      else if (typeof d.v === "string" && d.v.length <= JBC_MAX_BODY) localStorage.setItem(d.k, d.v);
    } else if (d.jb === "fetch") {
      const reply = (m) => e.source.postMessage({ jb: "res", id: d.id, ...m }, "*");
      try {
        const u = new URL(String(d.path), location.origin);
        const rel = u.pathname.slice(JBC_BASE.length);
        if (u.origin !== location.origin || !u.pathname.startsWith(JBC_BASE) || rel.split("/").includes(".."))
          throw new Error("not a Jandy Bridge request");
        if (d.method !== "GET" && !(d.method === "POST" && JBC_POSTS.has(rel))) throw new Error("not allowed: " + d.method + " " + rel);
        if (d.body != null && (typeof d.body !== "string" || d.body.length > JBC_MAX_BODY)) throw new Error("bad body");
        const init = { method: d.method };
        if (d.method === "POST") {
          init.headers = { "Content-Type": /^[\w.+-]+\/[\w.+-]+$/.test(d.type) ? d.type : "application/json" };
          init.body = d.body ?? "";
        }
        const r = await this._hass.fetchWithAuth(u.pathname + u.search, init);
        reply({ status: r.status, type: r.headers.get("Content-Type") || "", body: await r.text() });
      } catch (err) {
        reply({ error: String((err && err.message) || err) });
      }
    }
  }
}

if (!customElements.get("jandy-bridge-card")) customElements.define("jandy-bridge-card", JandyBridgeCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "jandy-bridge-card", name: "Jandy Bridge", description: "The Jandy Bridge salt + RS8 card, live from the bridge." });
