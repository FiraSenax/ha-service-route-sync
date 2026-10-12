/* Private WLAN history: authenticated HA websocket, text-only DOM rendering. */
export class WifiHistoryCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.source = "observations";
    this.generation = 0;
    this.previous = [];
    this.after = 0;
    this.ceiling = undefined;
    this.loading = false;
    this.inFlight = false;
    this.page = null;
    this.error = "";
  }

  setConfig(config) {
    if (typeof config.entry_id !== "string" || !config.entry_id.trim()) {
      throw new Error("Service Route Sync: entry_id fehlt");
    }
    this.config = { entry_id: config.entry_id };
    this.reset();
  }

  set hass(value) {
    const adminChanged = this._hass?.user?.is_admin !== value?.user?.is_admin;
    this._hass = value;
    if (!value?.user?.is_admin) {
      this.generation++;
      this.page = null;
      this.loading = false;
      this.render();
      return;
    }
    if (adminChanged) this.reset();
    else if (this.isConnected && !this.page && !this.loading && !this.error) this.load();
  }

  connectedCallback() { this.load(); }
  disconnectedCallback() { this.generation++; this.loading = false; }
  getCardSize() { return 6; }

  reset() {
    this.generation++;
    this.previous = [];
    this.after = 0;
    this.ceiling = undefined;
    this.page = null;
    this.error = "";
    this.loading = false;
    this.render();
    if (this.isConnected) this.load();
  }

  async load() {
    if (!this.config || !this._hass?.user?.is_admin || this.inFlight || !this.isConnected) return;
    const generation = this.generation;
    this.loading = true;
    this.inFlight = true;
    this.error = "";
    this.render();
    try {
      const request = { type: "service_route_sync/wifi_history", entry_id: this.config.entry_id,
        source: this.source, after: this.after, limit: 50 };
      if (this.ceiling !== undefined) request.ceiling = this.ceiling;
      let page;
      for (let attempt = 0; attempt < 4; attempt++) {
        try {
          page = await this._hass.callWS(request);
          break;
        } catch (error) {
          if (error?.code !== "busy" || attempt === 3) throw error;
          await new Promise(resolve => setTimeout(resolve, 100 * 2 ** attempt));
          if (generation !== this.generation) return;
        }
      }
      if (generation !== this.generation) return;
      if (!Array.isArray(page.columns) || page.columns.length !== 17 || !Array.isArray(page.rows)
          || page.rows.length > 50 || !page.rows.every(row => Array.isArray(row) && row.length === 17)
          || !Number.isSafeInteger(page.after) || !Number.isSafeInteger(page.ceiling)
          || page.after < this.after || page.ceiling < page.after || typeof page.more !== "boolean") {
        throw new Error("Invalid history page");
      }
      this.page = page;
      this.ceiling = page.ceiling;
    } catch {
      if (generation === this.generation) {
        this.page = null;
        this.error = "Historie konnte nicht geladen werden. Bitte erneut versuchen.";
      }
    } finally {
      this.inFlight = false;
      if (generation === this.generation) {
        this.loading = false;
        this.render();
      } else if (this.isConnected && this._hass?.user?.is_admin && !this.page && !this.error) {
        this.load();
      }
    }
  }

  render() {
    const root = this.shadowRoot;
    root.replaceChildren();
    const style = document.createElement("style");
    style.textContent = "ha-card{padding:16px}h2{font-size:20px;margin:0 0 12px}.controls{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.scroll{overflow:auto;max-height:480px}table{border-collapse:collapse;font-size:13px}th,td{padding:7px;text-align:left;border-bottom:1px solid var(--divider-color);white-space:nowrap}button,select{padding:8px;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:6px}p{color:var(--secondary-text-color)}";
    root.append(style);
    const card = document.createElement("ha-card");
    root.append(card);
    const title = document.createElement("h2");
    title.textContent = "WLAN-Historie";
    card.append(title);
    if (!this._hass?.user?.is_admin) {
      const note = document.createElement("p");
      note.textContent = "Diese Gerätehistorie ist nur für Administratoren verfügbar.";
      card.append(note);
      return;
    }
    const controls = document.createElement("div");
    controls.className = "controls";
    card.append(controls);
    const select = document.createElement("select");
    select.setAttribute("aria-label", "Historienquelle");
    for (const [value, name] of [["observations", "Neue HA-Messungen"], ["legacy", "Importierte Bestandsdaten"]]) {
      const option = document.createElement("option");
      option.value = value; option.textContent = name; select.append(option);
    }
    select.value = this.source;
    select.onchange = () => { this.source = select.value; this.reset(); };
    controls.append(select);
    const button = (name, disabled, action) => {
      const element = document.createElement("button");
      element.textContent = name; element.disabled = disabled; element.onclick = action;
      controls.append(element);
    };
    button("Aktualisieren", this.loading, () => this.reset());
    button("Zurück", this.loading || !this.previous.length, () => {
      this.after = this.previous.pop(); this.load();
    });
    button("Weiter", this.loading || !this.page?.more || this.previous.length >= 10000, () => {
      this.previous.push(this.after); this.after = this.page.after; this.load();
    });
    const note = document.createElement("p");
    note.textContent = this.loading ? "Wird geladen …" : this.error ||
      (this.page?.rows.length ? `${this.page.rows.length} Datensätze auf dieser Seite` : "Keine Datensätze vorhanden.");
    note.setAttribute("role", "status");
    card.append(note);
    if (!this.page || this.loading) return;
    const scroll = document.createElement("div"); scroll.className = "scroll";
    const table = document.createElement("table"); scroll.append(table); card.append(scroll);
    const header = document.createElement("tr");
    for (const column of this.page.columns) {
      const cell = document.createElement("th"); cell.textContent = String(column); header.append(cell);
    }
    table.append(header);
    for (const row of this.page.rows) {
      const line = document.createElement("tr");
      for (const value of row) {
        const cell = document.createElement("td"); cell.textContent = value === null ? "—" : String(value); line.append(cell);
      }
      table.append(line);
    }
  }
}

if (!customElements.get("service-route-sync-wifi-history")) {
  customElements.define("service-route-sync-wifi-history", WifiHistoryCard);
}
window.customCards = window.customCards || [];
if (!window.customCards.some(card => card.type === "service-route-sync-wifi-history")) {
  window.customCards.push({ type: "service-route-sync-wifi-history", name: "WLAN-Historie",
    description: "Geschützte WLAN-Historie von Service Route Sync" });
}
