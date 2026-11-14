/*
 * jandy-bridge-card.js  v1.1.0
 * The Jandy Bridge's own salt + RS8 card (the bridge page in its ?embed view), inside Home Assistant.
 * The integration serves and loads this file; the page and its API go through
 * /api/iaqualink/bridge/ with the HA login, so it works wherever HA does and needs no bridge password.
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

class JandyBridgeCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
  }

  set hass(hass) {
    this._hass = hass;
    window.__jbFetch = (path, init) => this._hass.fetchWithAuth(path, init);
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
    if (this._frame && !this._timer) {
      this._timer = setInterval(() => this._fit(), 300);
      this._check = setInterval(() => this._changed(), JBC_CHECK_MS);
      this._changed();
    }
    else if (this._started && !this._frame && !this._retry) this._load();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
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
    const boot =
      '<base href="' + base + '"><script>window.JB_EMBED="rs8";(()=>{const P=parent,o=P.location.origin;' +
      "window.fetch=(u,i)=>{const x=new URL(typeof u==\"string\"?u:u.url,document.baseURI);" +
      'return x.origin===o&&x.pathname.startsWith("' + JBC_BASE + '")?P.__jbFetch(x.pathname+x.search,i||{}):P.fetch(u,i)}})()<\/script>';
    html = /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, (m) => m + boot) : boot + html;
    const f = document.createElement("iframe");
    f.title = "Jandy Bridge";
    f.style.cssText = "border:0;width:100%;height:420px;display:block;background:transparent;color-scheme:light";
    f.srcdoc = html;
    this.replaceChildren(f);
    this._frame = f;
    clearInterval(this._timer);
    this._timer = setInterval(() => this._fit(), 300);
    clearInterval(this._check);
    this._check = setInterval(() => this._changed(), JBC_CHECK_MS);
  }

  _fit() {
    const f = this._frame, d = f && f.contentDocument;
    const c = d && d.getElementById("c-rs8");
    if (!c) return;
    const pop = d.querySelector("#rs8pop>.card");
    const h = Math.ceil(Math.max(c.getBoundingClientRect().bottom + d.defaultView.scrollY + 2, pop ? pop.scrollHeight / 0.94 + 24 : 0));
    if (Math.abs(f.offsetHeight - h) > 1) f.style.height = h + "px";
  }
}

if (!customElements.get("jandy-bridge-card")) customElements.define("jandy-bridge-card", JandyBridgeCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "jandy-bridge-card", name: "Jandy Bridge", description: "The Jandy Bridge salt + RS8 card, live from the bridge." });
