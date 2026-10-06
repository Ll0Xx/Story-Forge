async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// Pulls the "detail" out of a FastAPI error raised by api().
function errorText(e) {
  const m = String(e.message).match(/^\d+ (.*)$/s);
  try { return JSON.parse(m[1]).detail; } catch { return e.message; }
}

const BEGIN_PROMPT =
  '[Engine: the player pressed "Begin the adventure". Read WORLD.md, memory/recap.md and the db summary, ' +
  'then present the opening scene.]';

function app() {
  return {
    view: 'home',
    worlds: [],
    worldId: null,
    world: null,
    mode: null,
    messages: [],
    state: null,
    files: [],
    live: null,
    busy: false,
    input: '',
    health: null,
    login: { url: null, code: '', busy: false, error: '' },
    importing: false,
    ws: null,
    pollTimer: null,
    setupLabels: {
      setting: 'Setting', tone: 'Tone & style', rules: 'Core rules', combat: 'Conflict & combat',
      character: 'Your character', ui: 'Screen widgets', scripts: 'Scripts tested',
    },

    async init() {
      window.addEventListener('hashchange', () => this.route());
      api('/api/health').then(h => (this.health = h)).catch(() => {});
      await this.route();
    },

    async route() {
      const m = location.hash.match(/^#\/w\/([a-z0-9-]+)\/(build|play)$/);
      if (!m) {
        this.leaveWorld();
        this.view = 'home';
        this.worlds = await api('/api/worlds');
        return;
      }
      const [, id, mode] = m;
      if (id !== this.worldId) {
        this.leaveWorld();
        this.worldId = id;
        this.connect();
      }
      this.mode = mode;
      this.view = 'world';
      this.live = null;
      await this.load();
    },

    async load() {
      try {
        const d = await api(`/api/worlds/${this.worldId}?mode=${this.mode}`);
        Object.assign(this, { world: d.world, state: d.state, files: d.files, messages: d.messages });
        if (d.busy && !this.live) this.waitWhileBusy();
        else if (!this.live) this.busy = false;
        this.scroll();
      } catch (e) {
        location.hash = '#/';
      }
    },

    // A turn is running that this page didn't start (e.g. after reload): poll until it finishes.
    waitWhileBusy() {
      this.busy = true;
      clearTimeout(this.pollTimer);
      this.pollTimer = setTimeout(() => this.load(), 3000);
    },

    leaveWorld() {
      clearTimeout(this.pollTimer);
      if (this.ws) { this.ws.onclose = null; this.ws.close(); }
      Object.assign(this, { ws: null, worldId: null, world: null, mode: null, messages: [], live: null, busy: false });
    },

    connect() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(`${proto}://${location.host}/api/worlds/${this.worldId}/ws`);
      ws.onmessage = e => this.onEvent(JSON.parse(e.data));
      ws.onclose = () => {
        if (this.ws !== ws) return;
        this.ws = null;
        // The turn keeps running on the server; pick up the result when reconnected.
        if (this.live) { this.live = null; this.waitWhileBusy(); }
        setTimeout(() => { if (this.worldId && !this.ws) this.connect(); }, 1500);
      };
      this.ws = ws;
    },

    async socketReady() {
      for (let i = 0; i < 50 && (!this.ws || this.ws.readyState !== WebSocket.OPEN); i++) {
        await new Promise(r => setTimeout(r, 100));
      }
      return this.ws && this.ws.readyState === WebSocket.OPEN;
    },

    async send(text = this.input, display = null) {
      text = text.trim();
      if (this.busy || !text) return;
      this.busy = true;
      if (!(await this.socketReady())) {
        this.busy = false;
        this.messages.push({ role: 'error', text: 'Cannot connect to the server.' });
        return;
      }
      this.messages.push(display ? { role: 'system', text: display } : { role: 'user', text });
      if (!display) this.input = '';
      this.live = { asides: [], tools: [], text: '' };
      this.ws.send(JSON.stringify({ mode: this.mode, text, display }));
      this.scroll();
    },

    beginAdventure() {
      this.send(BEGIN_PROMPT, '— The adventure begins —');
    },

    onEvent(ev) {
      const live = this.live;
      switch (ev.type) {
        case 'text':
          if (live) live.text += ev.text;
          break;
        case 'tool':
          if (!live) break;
          // Text written before a tool call is GM "thinking aloud": keep it, but dimmed.
          if (live.text.trim()) live.asides.push(live.text);
          live.text = '';
          live.tools.push(ev);
          break;
        case 'tool_result':
          if (live && live.tools.length) live.tools[live.tools.length - 1].output = ev.output;
          break;
        case 'done':
        case 'error':
          if (ev.world) Object.assign(this, { world: ev.world, state: ev.state, files: ev.files });
          if (!ev.mode || ev.mode === this.mode) {
            this.messages.push(ev.type === 'done' ? ev.message : { role: 'error', text: ev.message });
          }
          this.live = null;
          this.busy = false;
          this.$nextTick(() => this.$refs.input && this.$refs.input.focus());
          break;
      }
      this.scroll();
    },

    async createWorld() {
      const w = await api('/api/worlds', { method: 'POST' });
      location.hash = `#/w/${w.id}/build`;
    },

    async importWorld(event) {
      const file = event.target.files[0];
      event.target.value = '';
      if (!file) return;
      this.importing = true;
      try {
        const w = await api('/api/worlds/import', { method: 'POST', body: file });
        this.worlds = await api('/api/worlds');
        alert(`Imported "${w.name}".`);
      } catch (e) {
        alert(`Import failed: ${errorText(e)}`);
      } finally {
        this.importing = false;
      }
    },

    async loginStart() {
      Object.assign(this.login, { busy: true, error: '', code: '' });
      try {
        this.login.url = (await api('/api/login/start', { method: 'POST' })).url;
        window.open(this.login.url, '_blank', 'noopener');
      } catch (e) {
        this.login.error = errorText(e);
      } finally {
        this.login.busy = false;
      }
    },

    async loginFinish() {
      Object.assign(this.login, { busy: true, error: '' });
      try {
        await api('/api/login/finish', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: this.login.code }),
        });
        this.health = await api('/api/health');
        this.login.url = null;
      } catch (e) {
        Object.assign(this.login, { error: errorText(e), url: null });
      } finally {
        this.login.busy = false;
      }
    },

    async deleteWorld(w) {
      if (!confirm(`Delete "${w.name}" permanently? All files and progress will be lost.`)) return;
      await api(`/api/worlds/${w.id}`, { method: 'DELETE' });
      this.worlds = await api('/api/worlds');
    },

    // ---- rendering helpers ----
    md(text) {
      return DOMPurify.sanitize(marked.parse(text || '', { breaks: true }));
    },

    toolLabel(t) {
      const input = (t.input || '').split('\n')[0];
      return `${t.name}: ${input.length > 90 ? input.slice(0, 90) + '…' : input}`;
    },

    statEntries(data) {
      const skip = new Set(['icon', 'description']);
      const fmt = v => (v && typeof v === 'object')
        ? (Array.isArray(v) ? v.join(', ') : Object.entries(v).map(([k, x]) => `${k} ${fmt(x)}`).join(' · '))
        : String(v);
      return Object.entries(data || {}).filter(([k]) => !skip.has(k)).map(([k, v]) => [k.replace(/_/g, ' '), fmt(v)]);
    },

    scroll() {
      this.$nextTick(() => {
        const log = this.$refs.log;
        if (log) log.scrollTop = log.scrollHeight;
      });
    },
  };
}
