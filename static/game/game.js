// BooksAI Agent Visualiser — Phaser 3 game
// Connects to Flask-SocketIO and animates 5 AI agents on an office floor.

const TILE   = 64;
const COLS   = 10;
const ROWS   = 8;
const W      = COLS * TILE;   // 640
const H      = ROWS * TILE;   // 512

// Agent definitions: id, emoji label, grid home position, accent colour
const AGENTS = [
  { id: 'inspector',  emoji: '🔍', col: 2, row: 2, color: 0x7070ff, label: 'Inspector' },
  { id: 'sorter',     emoji: '🗂️', col: 7, row: 2, color: 0xffaa40, label: 'Sorter'    },
  { id: 'scanner',    emoji: '📷', col: 2, row: 5, color: 0x40d4ff, label: 'Scanner'   },
  { id: 'advisor',    emoji: '💬', col: 5, row: 6, color: 0x4fffb0, label: 'Advisor'   },
  { id: 'strategist', emoji: '📊', col: 7, row: 5, color: 0xff60a0, label: 'Strategist'},
];

// Room labels drawn under each agent home tile
const ROOMS = [
  { col: 2, row: 1, label: 'DATA DESK'       },
  { col: 7, row: 1, label: 'FILING ROOM'     },
  { col: 2, row: 4, label: 'SCAN STATION'    },
  { col: 5, row: 5, label: 'HELP DESK'       },
  { col: 7, row: 4, label: 'STRATEGY BOARD'  },
];

// Shared event queue filled by the Socket.IO listener
const eventQueue = [];
let socket;

// ------------------------------------------------------------------
// Phaser scene
// ------------------------------------------------------------------
class OfficeScene extends Phaser.Scene {
  constructor() { super({ key: 'OfficeScene' }); }

  preload() {
    // Build textures programmatically — no external assets needed
    const g = this.make.graphics({ x: 0, y: 0, add: false });

    // Floor tile A (dark)
    g.fillStyle(0x1a1a2e, 1);
    g.fillRect(0, 0, TILE, TILE);
    g.lineStyle(1, 0x22224a, 1);
    g.strokeRect(0, 0, TILE, TILE);
    g.generateTexture('tileA', TILE, TILE);

    // Floor tile B (slightly lighter)
    g.clear();
    g.fillStyle(0x1e1e36, 1);
    g.fillRect(0, 0, TILE, TILE);
    g.lineStyle(1, 0x22224a, 1);
    g.strokeRect(0, 0, TILE, TILE);
    g.generateTexture('tileB', TILE, TILE);

    // Desk/room highlight tile
    g.clear();
    g.fillStyle(0x22224a, 1);
    g.fillRect(0, 0, TILE, TILE);
    g.lineStyle(1, 0x3a3a6a, 1);
    g.strokeRect(0, 0, TILE, TILE);
    g.generateTexture('tileRoom', TILE, TILE);

    // Agent circle base
    g.clear();
    g.fillStyle(0xffffff, 1);
    g.fillCircle(24, 24, 22);
    g.generateTexture('agentBase', 48, 48);

    // Pulse ring
    g.clear();
    g.lineStyle(3, 0xffffff, 1);
    g.strokeCircle(32, 32, 28);
    g.generateTexture('ring', 64, 64);

    g.destroy();
  }

  create() {
    this._agents = {};    // agent state objects keyed by id
    this._tweens = {};    // active tweens per agent

    this._drawFloor();
    this._drawRoomLabels();
    this._spawnAgents();
    this._connectSocket();
  }

  // ── Floor ────────────────────────────────────────────────────────
  _drawFloor() {
    // Checkerboard base
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        const key = (r + c) % 2 === 0 ? 'tileA' : 'tileB';
        this.add.image(c * TILE + TILE / 2, r * TILE + TILE / 2, key);
      }
    }
    // Room highlight tiles (2×2 around each agent home)
    const highlighted = new Set();
    AGENTS.forEach(a => {
      for (let dr = -1; dr <= 1; dr++) {
        for (let dc = -1; dc <= 1; dc++) {
          const key = `${a.col + dc},${a.row + dr}`;
          if (!highlighted.has(key)) {
            highlighted.add(key);
            this.add.image((a.col + dc) * TILE + TILE / 2,
                           (a.row + dr) * TILE + TILE / 2, 'tileRoom');
          }
        }
      }
    });
    // Room border rectangles
    AGENTS.forEach(a => {
      const rect = this.add.rectangle(
        a.col * TILE + TILE / 2,
        a.row * TILE + TILE / 2,
        TILE * 2.6, TILE * 2.6, 0x000000, 0
      );
      rect.setStrokeStyle(1.5, a.color, 0.35);
    });
  }

  _drawRoomLabels() {
    ROOMS.forEach(r => {
      this.add.text(
        r.col * TILE + TILE / 2, r.row * TILE + 6,
        r.label,
        { font: '9px Courier New', fill: '#505080', align: 'center' }
      ).setOrigin(0.5, 0);
    });
  }

  // ── Agents ──────────────────────────────────────────────────────
  _spawnAgents() {
    AGENTS.forEach(def => {
      const x = def.col * TILE + TILE / 2;
      const y = def.row * TILE + TILE / 2;

      // Coloured circle
      const circle = this.add.circle(x, y, 20, def.color, 1);

      // Emoji label
      const emojiText = this.add.text(x, y - 2, def.emoji,
        { font: '18px serif', align: 'center' }).setOrigin(0.5, 0.5);

      // Name label below
      const nameText = this.add.text(x, y + 26, def.label,
        { font: '9px Courier New', fill: '#a0a0cc', align: 'center' }
      ).setOrigin(0.5, 0);

      // Pulse ring (hidden by default)
      const ring = this.add.image(x, y, 'ring').setAlpha(0).setTint(def.color);

      // Thought bubble (hidden)
      const bubbleBg = this.add.rectangle(x, y - 46, 120, 26, 0x2a2a5a, 0.92)
        .setStrokeStyle(1, def.color, 0.8).setVisible(false);
      const bubbleText = this.add.text(x, y - 46, '',
        { font: '9px Courier New', fill: '#e0e0ff', wordWrap: { width: 110 }, align: 'center' }
      ).setOrigin(0.5, 0.5).setVisible(false);

      // Status glow behind circle (hidden by default)
      const glow = this.add.circle(x, y, 28, def.color, 0).setDepth(-1);

      // Idle float tween
      const floatTween = this.tweens.add({
        targets: [circle, emojiText, ring, bubbleBg, bubbleText, glow],
        y: `+=${6}`,
        duration: 1400 + Math.random() * 400,
        yoyo: true,
        repeat: -1,
        ease: 'Sine.easeInOut',
        offset: Math.random() * 1400,
      });

      this._agents[def.id] = {
        def, circle, emojiText, nameText, ring, bubbleBg, bubbleText,
        glow, floatTween, x, y, state: 'idle',
      };
    });
  }

  // ── Socket.IO ────────────────────────────────────────────────────
  _connectSocket() {
    socket = io({ transports: ['websocket', 'polling'] });

    socket.on('connect', () => {
      document.getElementById('conn-status').textContent = '● Connected';
      document.getElementById('conn-status').className = 'connected';
    });
    socket.on('disconnect', () => {
      document.getElementById('conn-status').textContent = '● Disconnected';
      document.getElementById('conn-status').className = 'disconnected';
    });
    socket.on('agent_event', (data) => eventQueue.push(data));
  }

  // ── Update loop ──────────────────────────────────────────────────
  update() {
    while (eventQueue.length > 0) {
      this._handleEvent(eventQueue.shift());
    }
  }

  _handleEvent(ev) {
    const a = this._agents[ev.agent_id];
    if (!a) return;

    this._addLogEntry(ev);
    this._updateCard(ev);

    switch (ev.event_type) {
      case 'start':    this._onStart(a, ev);    break;
      case 'tool_use': this._onToolUse(a, ev);  break;
      case 'complete': this._onComplete(a, ev); break;
      case 'error':    this._onError(a, ev);    break;
    }
  }

  // ── Event handlers ───────────────────────────────────────────────
  _onStart(a, ev) {
    a.state = 'active';
    // Brighten circle
    this.tweens.add({
      targets: a.circle, alpha: 1, duration: 200,
      onComplete: () => a.circle.setAlpha(1),
    });
    a.circle.setFillStyle(a.def.color, 1);
    // Pulsing glow
    a.glow.setFillStyle(a.def.color, 0.25);
    if (this._tweens[a.def.id + '_pulse']) this._tweens[a.def.id + '_pulse'].stop();
    this._tweens[a.def.id + '_pulse'] = this.tweens.add({
      targets: a.glow, alpha: { from: 0.1, to: 0.4 },
      duration: 600, yoyo: true, repeat: -1, ease: 'Sine.easeInOut',
    });
    // Pulse ring fade in
    a.ring.setAlpha(0.6);
    this.tweens.add({
      targets: a.ring, scaleX: 1.4, scaleY: 1.4, alpha: 0,
      duration: 900, repeat: -1, ease: 'Cubic.easeOut',
    });
    this._showBubble(a, ev.message, 2500);
  }

  _onToolUse(a, ev) {
    // Flash ring bright
    this.tweens.add({
      targets: a.ring, alpha: { from: 1, to: 0 }, scaleX: 1.6, scaleY: 1.6,
      duration: 600, ease: 'Cubic.easeOut',
    });
    this._showBubble(a, ev.message, 3000);
  }

  _onComplete(a, ev) {
    a.state = 'idle';
    this._stopPulse(a);
    // Green flash
    a.circle.setFillStyle(0x4fffb0, 1);
    this.tweens.add({
      targets: a.circle, alpha: 0.6, duration: 300, yoyo: true, repeat: 2,
      onComplete: () => a.circle.setFillStyle(a.def.color, 1),
    });
    // Particle burst (simple expanding circles)
    for (let i = 0; i < 6; i++) {
      const angle = (i / 6) * Math.PI * 2;
      const dot = this.add.circle(a.x, a.y, 4, 0x4fffb0, 1);
      this.tweens.add({
        targets: dot,
        x: a.x + Math.cos(angle) * 40,
        y: a.y + Math.sin(angle) * 40,
        alpha: 0, duration: 600, ease: 'Cubic.easeOut',
        onComplete: () => dot.destroy(),
      });
    }
    a.glow.setAlpha(0);
    this._showBubble(a, '✓ ' + ev.message, 2000);
  }

  _onError(a, ev) {
    a.state = 'error';
    this._stopPulse(a);
    // Red shake
    a.circle.setFillStyle(0xff4040, 1);
    this.tweens.add({
      targets: [a.circle, a.emojiText],
      x: `+=${8}`, duration: 80, yoyo: true, repeat: 5,
      onComplete: () => {
        a.circle.setFillStyle(a.def.color, 1);
        [a.circle, a.emojiText].forEach(o => o.setX(a.x));
      },
    });
    a.glow.setAlpha(0);
    this._showBubble(a, '⚠ ' + ev.message, 3000);
  }

  // ── Helpers ──────────────────────────────────────────────────────
  _stopPulse(a) {
    const key = a.def.id + '_pulse';
    if (this._tweens[key]) { this._tweens[key].stop(); delete this._tweens[key]; }
    a.ring.setAlpha(0).setScale(1);
    a.glow.setAlpha(0);
  }

  _showBubble(a, text, duration) {
    // Truncate long messages for the bubble
    const label = text.length > 55 ? text.slice(0, 52) + '…' : text;
    a.bubbleText.setText(label);
    a.bubbleBg.setVisible(true);
    a.bubbleText.setVisible(true);
    if (a._bubbleTimer) clearTimeout(a._bubbleTimer);
    a._bubbleTimer = setTimeout(() => {
      a.bubbleBg.setVisible(false);
      a.bubbleText.setVisible(false);
    }, duration);
  }

  // ── Sidebar helpers (DOM) ────────────────────────────────────────
  _updateCard(ev) {
    const card   = document.getElementById(`card-${ev.agent_id}`);
    const status = document.getElementById(`status-${ev.agent_id}`);
    const dot    = document.getElementById(`dot-${ev.agent_id}`);
    if (!card || !status || !dot) return;

    const msg = ev.message.length > 40 ? ev.message.slice(0, 37) + '…' : ev.message;
    status.textContent = msg;

    card.className  = 'agent-card';
    dot.className   = 'agent-dot';
    if (ev.event_type === 'start' || ev.event_type === 'tool_use') {
      card.classList.add('active');
      dot.classList.add('active');
    } else if (ev.event_type === 'complete') {
      card.classList.add('complete');
      dot.classList.add('complete');
      setTimeout(() => {
        card.className = 'agent-card';
        dot.className  = 'agent-dot idle';
        status.textContent = 'Idle';
      }, 3000);
    } else if (ev.event_type === 'error') {
      card.classList.add('error');
      dot.classList.add('error');
    }
  }

  _addLogEntry(ev) {
    const log   = document.getElementById('activity-log');
    const entry = document.createElement('div');
    entry.className = `log-entry ${ev.event_type}`;

    const ts   = new Date(ev.timestamp + 'Z');
    const time = ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const agentName = AGENTS.find(a => a.id === ev.agent_id)?.label || ev.agent_id;

    entry.innerHTML = `
      <div class="log-time">${time}</div>
      <div><span class="log-agent">${agentName}</span></div>
      <div class="log-msg">${ev.message}</div>
    `;
    // Keep newest at top
    log.insertBefore(entry, log.children[1] || null);
    // Cap log at 60 entries
    while (log.children.length > 61) log.removeChild(log.lastChild);
  }
}

// ------------------------------------------------------------------
// Boot
// ------------------------------------------------------------------
const config = {
  type:            Phaser.AUTO,
  width:           W,
  height:          H,
  backgroundColor: '#0d0d1a',
  parent:          'game-container',
  scene:           OfficeScene,
  scale: {
    mode:       Phaser.Scale.FIT,
    autoCenter: Phaser.Scale.CENTER_BOTH,
  },
};

new Phaser.Game(config);
