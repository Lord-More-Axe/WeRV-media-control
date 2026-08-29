/* WeRV remote client.
 *
 * Two ideas do most of the work here.
 *
 * The server pushes state over one Server-Sent Events connection instead of the page
 * asking three times a second, so an open phone costs an idle socket.
 *
 * And the scrubber never asks anything at all: each state carries the position, how long
 * ago it was measured, and whether the clock is running, so the client can advance the
 * position itself. That also means no clock synchronisation between the PC and the phone,
 * because nothing absolute is ever compared.
 */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const body = document.body;
  const root = document.documentElement;

  const el = {
    art: $('art'), artA: $('artA'), artB: $('artB'),
    title: $('title'), byline: $('byline'),
    seek: $('seek'), seekFill: $('seekFill'), seekKnob: $('seekKnob'),
    elapsed: $('elapsed'), remaining: $('remaining'),
    toggle: $('toggle'), toggleIcon: $('toggleIcon'),
    prev: $('prev'), next: $('next'),
    shuffle: $('shuffle'), repeat: $('repeat'), repeatIcon: $('repeatIcon'),
    volume: $('volume'), vol: $('vol'), volFill: $('volFill'), volKnob: $('volKnob'),
    mute: $('mute'), muteIcon: $('muteIcon'),
    source: $('source'), sourceName: $('sourceName'),
    sourceSheet: $('sourceSheet'), sourceList: $('sourceList'),
    pair: $('pair'), pairSheet: $('pairSheet'), qrHolder: $('qrHolder'),
    lockscreen: $('lockscreen'),
    pairUrl: $('pairUrl'), copyUrl: $('copyUrl'), altUrls: $('altUrls'),
    empty: $('empty'), emptyTitle: $('emptyTitle'), emptyBody: $('emptyBody'),
    toast: $('toast'), themeColor: document.querySelector('meta[name="theme-color"]'),
  };

  let state = null;
  let receivedAt = 0;          // performance.now() when the current state arrived
  let artUrl = null;
  let artFront = el.artA;
  let optimisticUntil = 0;     // hold our own guess until the server catches up
  let optimisticGuess = null;  // what we guessed, so a matching frame can end the hold
  let sourceSignature = '';    // so the source list is not rebuilt on every push

  /* ------------------------------------------------------------------ utils -- */

  const clamp = (n, lo, hi) => Math.min(Math.max(n, lo), hi);

  function formatTime(seconds) {
    if (!isFinite(seconds) || seconds < 0) seconds = 0;
    const total = Math.floor(seconds);
    const s = total % 60, m = Math.floor(total / 60) % 60, h = Math.floor(total / 3600);
    const pad = (n) => String(n).padStart(2, '0');
    return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }

  function buzz(ms = 8) {
    if (navigator.vibrate) { try { navigator.vibrate(ms); } catch (_) { /* unsupported */ } }
  }

  let toastTimer = 0;
  function toast(message) {
    el.toast.textContent = message;
    el.toast.classList.add('shown');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.toast.classList.remove('shown'), 2200);
  }

  /* --------------------------------------------------------------- commands -- */

  async function send(action, extra = {}) {
    try {
      const response = await fetch('/api/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-WeRV': '1' },
        body: JSON.stringify({ action, ...extra }),
        credentials: 'same-origin',
      });
      if (response.status === 401) { location.reload(); return false; }
      const data = await response.json().catch(() => ({}));
      // The player declined -- usually because the app does not support that control.
      if (data && data.ok === false) toast('That app would not accept it');
      return !!(data && data.ok);
    } catch (_) {
      toast('Lost the connection');
      return false;
    }
  }

  /* ------------------------------------------------------------------ rails --
   * Shared by the scrubber and the volume slider. Pointer events give us mouse, touch
   * and pen in one path, and pointer capture keeps the drag alive when a thumb slides
   * off the 22px strip -- which it always does. */

  function makeRail(rail, fill, knob, { onPreview, onCommit, onAbort, enabled, describe }) {
    let dragging = false;
    let commitTimer = 0;

    const ratioAt = (event) => {
      const box = rail.getBoundingClientRect();
      // null, not 0: a rail that has been hidden measures zero wide, and treating that
      // as "the very beginning" would seek the track to 0:00 on release.
      return box.width ? clamp((event.clientX - box.left) / box.width, 0, 1) : null;
    };

    const paint = (ratio) => {
      fill.style.transform = `scaleX(${ratio})`;
      knob.style.left = `${ratio * 100}%`;
      rail.setAttribute('aria-valuenow', Math.round(ratio * 100));
      // Without this a screen reader says "47" for a position that means "1:32 of 3:20".
      if (describe) rail.setAttribute('aria-valuetext', describe(ratio));
    };

    rail.addEventListener('pointerdown', (event) => {
      if (!enabled()) return;
      dragging = true;
      rail.classList.add('dragging');
      rail.setPointerCapture(event.pointerId);
      const ratio = ratioAt(event);
      paint(ratio); onPreview(ratio);
      buzz(5);
      event.preventDefault();
    });

    rail.addEventListener('pointermove', (event) => {
      if (!dragging) return;
      const ratio = ratioAt(event);
      paint(ratio); onPreview(ratio);
    });

    const release = (event) => {
      dragging = false;
      rail.classList.remove('dragging');
      try { rail.releasePointerCapture(event.pointerId); } catch (_) { /* already gone */ }
    };

    const abort = () => { if (onAbort) onAbort(); };

    rail.addEventListener('pointerup', (event) => {
      if (!dragging) return;
      const ratio = ratioAt(event);
      release(event);
      if (ratio === null) { abort(); return; }
      onCommit(ratio);
      buzz(10);
    });

    // pointercancel means the gesture was taken away -- a system edge-swipe, an incoming
    // call. The user never let go, so committing here would seek somewhere they did not
    // choose. Put the rail back where it was instead.
    rail.addEventListener('pointercancel', (event) => {
      if (!dragging) return;
      release(event);
      abort();
    });

    // If the rail is removed under the finger -- the track ends, the player app closes,
    // and the whole control is hidden -- no pointerup will ever arrive here. Losing
    // capture is the only notice we get that the gesture is over.
    rail.addEventListener('lostpointercapture', () => {
      if (!dragging) return;
      dragging = false;
      rail.classList.remove('dragging');
      abort();
    });

    rail.addEventListener('keydown', (event) => {
      if (!enabled()) return;
      const step = event.shiftKey ? 0.1 : 0.02;
      const current = Number(rail.getAttribute('aria-valuenow') || 0) / 100;
      let next = current;
      if (event.key === 'ArrowRight' || event.key === 'ArrowUp') next = clamp(current + step, 0, 1);
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') next = clamp(current - step, 0, 1);
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = 1;
      else return;
      event.preventDefault();
      paint(next); onPreview(next);
      // Held arrow keys repeat many times a second; preview each one but only send the
      // position the user settles on.
      clearTimeout(commitTimer);
      commitTimer = setTimeout(() => onCommit(next), 180);
    });

    return {
      paint,
      isDragging: () => dragging,
      /** Give up a drag the interface can no longer support. */
      cancel() {
        if (!dragging) return;
        dragging = false;
        rail.classList.remove('dragging');
        abort();
      },
    };
  }

  /* --------------------------------------------------------------- scrubber -- */

  let scrubPreview = null;     // seconds being dragged to, or null
  let scrubHoldUntil = 0;      // keep showing our own value until the server agrees

  // The server already decides this: it knows the app's capability flags and the
  // window the player will actually accept a position within.
  const seekable = () => !!(state && state.timeline.seekable);

  const seekRail = makeRail(el.seek, el.seekFill, el.seekKnob, {
    enabled: seekable,
    describe: (ratio) => {
      const duration = state ? state.timeline.duration : 0;
      return duration > 0
        ? `${formatTime(ratio * duration)} of ${formatTime(duration)}`
        : formatTime(livePosition());
    },
    onPreview: (ratio) => { scrubPreview = ratio * state.timeline.duration; renderTime(); },
    onCommit: (ratio) => {
      const seconds = ratio * state.timeline.duration;
      scrubPreview = seconds;
      scrubHoldUntil = performance.now() + 1200;
      send('seek', { seconds });
      schedule();
    },
    onAbort: () => { scrubPreview = null; renderTime(); },
  });

  function livePosition() {
    if (!state) return 0;
    const { position, age, rate, duration } = state.timeline;
    const elapsed = (performance.now() - receivedAt) / 1000;
    // Both delays are wall-clock seconds, and the media only advanced through them at
    // `rate`. Adding `age` unscaled meant a paused track jumped forward by however long
    // ago Windows last reported its position -- which can be tens of seconds.
    const value = position + (age + elapsed) * rate;
    return duration > 0 ? clamp(value, 0, duration) : Math.max(0, value);
  }

  function renderTime() {
    if (!state) return;
    const duration = state.timeline.duration;
    const position = scrubPreview !== null ? scrubPreview : livePosition();

    el.elapsed.textContent = formatTime(position);
    if (duration > 0) {
      el.remaining.textContent = `-${formatTime(Math.max(0, duration - position))}`;
      if (!seekRail.isDragging()) seekRail.paint(clamp(position / duration, 0, 1));
    } else {
      // Live stream or an app that reports no length: count up, offer no scrubbing.
      el.remaining.textContent = state.status === 'playing' ? 'live' : '';
      if (!seekRail.isDragging()) seekRail.paint(0);
    }
  }

  let frame = 0;

  /** Is anything actually moving? A paused track advances by nothing. */
  const animating = () => !!state && (state.timeline.rate !== 0 || scrubPreview !== null);

  function tick() {
    frame = 0;
    if (document.hidden) return;
    if (scrubPreview !== null && !seekRail.isDragging() && performance.now() > scrubHoldUntil) {
      scrubPreview = null;
    }
    renderTime();
    schedule();
  }

  function schedule() {
    if (document.hidden) return;
    // Paint one last frame and stop. A loop running at the refresh rate for hours on a
    // paused player is pure battery cost on a page designed to be left open.
    if (!animating()) { renderTime(); return; }
    if (!frame) frame = requestAnimationFrame(tick);
  }

  /* ----------------------------------------------------------------- volume -- */

  let volumePreview = null;
  let volumeHoldUntil = 0;

  const volRail = makeRail(el.vol, el.volFill, el.volKnob, {
    enabled: () => !!(state && state.volume.available),
    describe: (ratio) => `${Math.round(ratio * 100)}% volume`,
    onPreview: (ratio) => { volumePreview = ratio; },
    onCommit: (ratio) => {
      volumePreview = ratio;
      volumeHoldUntil = performance.now() + 900;
      send('set_volume', { level: ratio });
    },
    onAbort: () => { volumePreview = null; renderVolume(); },
  });

  function renderVolume() {
    const volume = state && state.volume;
    el.volume.hidden = !(volume && volume.available);
    if (!volume || !volume.available) return;

    const holding = volRail.isDragging() || performance.now() < volumeHoldUntil;
    const level = holding && volumePreview !== null ? volumePreview : volume.level;
    if (!holding) volumePreview = null;

    if (!volRail.isDragging()) volRail.paint(volume.muted ? 0 : level);
    el.mute.setAttribute('aria-pressed', String(!!volume.muted));
    el.mute.setAttribute('aria-label', volume.muted ? 'Unmute' : 'Mute');
    const icon = volume.muted ? '#i-vol-mute' : level < 0.45 ? '#i-vol-low' : '#i-vol';
    el.muteIcon.setAttribute('href', icon);
  }

  /* ------------------------------------------------------------------ theme -- */

  let paletteKey = '';
  function applyPalette(palette) {
    if (!palette) return;
    const key = [palette.base, palette.accent, palette.onAccent, ...(palette.glow || [])].join('|');
    if (key === paletteKey) return;
    paletteKey = key;

    root.style.setProperty('--base', palette.base);
    root.style.setProperty('--accent', palette.accent);
    root.style.setProperty('--on-accent', palette.onAccent);
    (palette.glow || []).forEach((colour, i) => root.style.setProperty(`--glow-${i + 1}`, colour));
    // Match the browser chrome and the iOS status bar to the page.
    if (el.themeColor) el.themeColor.setAttribute('content', palette.base);
  }

  /* -------------------------------------------------------------------- art -- */

  function applyArt(url) {
    if (url === artUrl) return;
    artUrl = url;
    if (!url) {
      el.art.classList.remove('has-art');
      el.artA.classList.remove('shown');
      el.artB.classList.remove('shown');
      return;
    }
    // Cross-fade by loading into whichever layer is currently behind.
    const back = artFront === el.artA ? el.artB : el.artA;
    let revealed = false;
    const reveal = () => {
      // An image already in the browser cache reports `complete` synchronously *and*
      // fires onload. Running twice promoted the back layer and then stripped that same
      // layer's `shown` on the second pass, leaving no artwork visible at all -- which
      // is what happened as soon as a playlist wrapped back to a cover already seen.
      if (revealed || artUrl !== url) return;
      revealed = true;
      back.classList.add('shown');
      artFront.classList.remove('shown');
      artFront = back;
      el.art.classList.add('has-art');
    };
    back.onload = reveal;
    back.onerror = () => { if (artUrl === url) el.art.classList.remove('has-art'); };
    back.src = url;
    if (back.complete && back.naturalWidth) reveal();
  }

  /* ------------------------------------------------------------------ state -- */

  function apply(next) {
    // The initial snapshot a new subscriber is handed and the watcher's next broadcast
    // are produced by different threads, so they can arrive out of order. `rev` only
    // ever increases, which is enough to drop the loser.
    if (state && typeof next.rev === 'number' && typeof state.rev === 'number'
        && next.rev < state.rev) return;

    state = next;
    receivedAt = performance.now();

    // Once the server reports what we predicted, the hold has done its job -- keeping
    // it would go on ignoring frames, including the one carrying a correction if the
    // app turned the command down.
    if (optimisticGuess && next.status === optimisticGuess) {
      optimisticUntil = 0;
      optimisticGuess = null;
    }
    const holding = performance.now() < optimisticUntil;
    if (!holding) body.dataset.status = next.status;
    applyPalette(next.palette);
    applyArt(next.track.art);

    const track = next.track;
    const idle = next.status === 'idle' || next.status === 'unavailable';
    document.title = idle || !track.title ? 'WeRV' : `${track.title} — ${track.artist || 'WeRV'}`;

    el.title.textContent = track.title || (idle ? '' : 'Unknown track');
    const parts = [track.artist, track.album].filter(Boolean);
    el.byline.textContent = parts.join(' · ') || ' ';

    if (next.status === 'unavailable') {
      el.emptyTitle.textContent = 'Cannot reach the player';
      el.emptyBody.textContent = next.message || 'The media service on this PC is not responding.';
    } else {
      el.emptyTitle.textContent = 'Nothing playing';
      el.emptyBody.textContent = next.message ||
        'Start something on your PC — music, a video, a podcast — and it will appear here.';
    }

    const can = next.can;
    const playing = next.status === 'playing';
    el.toggle.disabled = !(can.toggle || can.play || can.pause);
    if (!holding) {
      el.toggleIcon.setAttribute('href', playing ? '#i-pause' : '#i-play');
      el.toggle.setAttribute('aria-label', playing ? 'Pause' : 'Play');
    }
    el.prev.disabled = !can.previous;
    el.next.disabled = !can.next;

    el.shuffle.disabled = !can.shuffle;
    el.shuffle.setAttribute('aria-pressed', String(!!next.shuffle));
    el.repeat.disabled = !can.repeat;
    const repeat = next.repeat || 'off';
    el.repeat.setAttribute('aria-pressed', String(repeat !== 'off'));
    el.repeat.setAttribute('aria-label',
      repeat === 'one' ? 'Repeat one' : repeat === 'all' ? 'Repeat all' : 'Repeat');
    el.repeatIcon.setAttribute('href', repeat === 'one' ? '#i-repeat-one' : '#i-repeat');

    el.seek.setAttribute('aria-disabled', String(!seekable()));

    // The rails can be removed from the page by a state change -- a track ending hides
    // the scrubber -- and a finger already down on one would otherwise never see a
    // pointerup, leaving the drag live forever.
    if (!seekable()) seekRail.cancel();
    if (!(next.volume && next.volume.available)) volRail.cancel();

    lockscreen.sync(next);
    renderVolume();
    renderSources(next);
    renderTime();
    schedule();
  }

  function renderSources(next) {
    const sessions = next.sessions || [];
    const current = sessions.find((s) => s.current) || sessions[0];
    el.sourceName.textContent = current ? current.name : (next.status === 'unavailable' ? 'Unavailable' : 'No source');
    el.source.disabled = sessions.length < 2;

    // Only rebuild when the set actually changed: replacing these nodes on every push
    // would cancel a tap the user is in the middle of making.
    const signature = sessions.map((s) => `${s.id}:${s.current}:${s.playing}`).join('|');
    if (signature === sourceSignature) return;
    sourceSignature = signature;

    el.sourceList.replaceChildren(...sessions.map((session) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.setAttribute('aria-current', String(!!session.current));
      const dot = document.createElement('i'); dot.className = 'dot';
      const name = document.createElement('span'); name.className = 'name'; name.textContent = session.name;
      button.append(dot, name);
      if (session.playing) {
        const now = document.createElement('span'); now.className = 'now'; now.textContent = 'playing';
        button.append(now);
      }
      button.addEventListener('click', () => {
        send('select_session', { session_id: session.id });
        el.sourceSheet.close();
      });
      const item = document.createElement('li');
      item.append(button);
      return item;
    }));
  }

  /* ------------------------------------------------------------------ stream --
   * EventSource reconnects on its own, but it cannot tell us it is trying, and iOS
   * quietly kills the socket when the page is backgrounded. So we track the link state
   * ourselves and re-open on the way back to the foreground. */

  let source = null;
  let lostSince = 0;
  let retry = 0;
  let retryTimer = 0;
  let lastMessage = 0;

  // A half-open socket -- a phone that woke up on a different access point, a router
  // that dropped the flow -- never raises an error. The stream just goes quiet. Since
  // the server re-sends state at least every ten seconds, a longer silence is a dead
  // connection wearing a live one's clothes.
  const SILENCE_LIMIT = 40000;

  // Backoff matters more than it looks. Every open stream holds a server worker thread
  // for its whole life, and a dropped one is not noticed until the next keepalive write
  // ~15s later -- so a phone that retries every two seconds through a real outage stacks
  // up zombie subscribers faster than they can be reaped. Backing off to 30s keeps a
  // flapping client from starving the very pool it needs to reconnect through.
  const RETRY_MIN = 1000;
  const RETRY_MAX = 30000;

  function scheduleReconnect() {
    if (retryTimer) return;
    // Exponential with full jitter, so several phones that dropped together when the PC
    // slept do not all come back on the same tick.
    const ceiling = Math.min(RETRY_MAX, RETRY_MIN * 2 ** retry);
    const delay = RETRY_MIN + Math.random() * (ceiling - RETRY_MIN);
    retry = Math.min(retry + 1, 6);
    retryTimer = setTimeout(() => { retryTimer = 0; connect(); }, delay);
  }

  function connect() {
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = 0; }
    if (source) { try { source.close(); } catch (_) { /* already closed */ } }
    source = new EventSource('/events', { withCredentials: true });

    source.onopen = () => { lostSince = 0; retry = 0; lastMessage = Date.now(); body.dataset.link = 'live'; };

    source.onmessage = (event) => {
      lostSince = 0;
      retry = 0;
      lastMessage = Date.now();
      body.dataset.link = 'live';
      try { apply(JSON.parse(event.data)); } catch (_) { /* ignore a partial frame */ }
    };

    source.onerror = () => {
      // A blip is not worth showing; a real outage is.
      if (!lostSince) lostSince = Date.now();
      if (Date.now() - lostSince > 1500) body.dataset.link = 'lost';
      // EventSource reconnects itself while the connection is merely interrupted; once
      // it gives up and closes, reconnecting is ours to do -- on a backoff.
      if (source.readyState === EventSource.CLOSED) scheduleReconnect();
    };
  }

  setInterval(() => {
    if (document.hidden || !lastMessage || retryTimer) return;
    if (Date.now() - lastMessage > SILENCE_LIMIT) {
      body.dataset.link = 'lost';
      // Push the mark forward before scheduling, so a PC that stays asleep does not
      // make this fire every five seconds and step around the backoff it exists beside.
      lastMessage = Date.now();
      scheduleReconnect();
    }
  }, 5000);

  async function refresh() {
    try {
      const response = await fetch('/api/state', { credentials: 'same-origin' });
      if (response.status === 401) { location.reload(); return; }
      apply(await response.json());
    } catch (_) { /* the stream will catch us up */ }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      if (frame) { cancelAnimationFrame(frame); frame = 0; }
      return;
    }
    schedule();
    refresh();
    if (!source || source.readyState === EventSource.CLOSED) connect();
  });

  /* ---------------------------------------------------------------- controls -- */

  el.toggle.addEventListener('click', async () => {
    buzz(12);
    // Flip immediately: on a LAN the round trip is short but not invisible, and a
    // transport button that waits for the server feels broken.
    const playing = body.dataset.status === 'playing';
    optimisticGuess = playing ? 'paused' : 'playing';
    body.dataset.status = optimisticGuess;
    el.toggleIcon.setAttribute('href', playing ? '#i-play' : '#i-pause');
    el.toggle.setAttribute('aria-label', playing ? 'Play' : 'Pause');
    optimisticUntil = performance.now() + 900;

    if (!(await send('toggle'))) {
      // The app refused. Stop guessing at once so the next frame puts the button back
      // where it belongs, rather than leaving it lying for the rest of the window.
      optimisticUntil = 0;
      optimisticGuess = null;
      refresh();
    }
  });

  el.prev.addEventListener('click', () => { buzz(); send('previous'); });
  el.next.addEventListener('click', () => { buzz(); send('next'); });
  el.shuffle.addEventListener('click', () => {
    buzz();
    send('set_shuffle', { on: !(state && state.shuffle) });
  });
  el.repeat.addEventListener('click', () => {
    buzz();
    const order = { off: 'all', all: 'one', one: 'off' };
    send('set_repeat', { mode: order[(state && state.repeat) || 'off'] });
  });
  el.mute.addEventListener('click', () => {
    buzz();
    send('set_muted', { muted: !(state && state.volume.muted) });
  });

  /* ------------------------------------------------------------------ sheets -- */

  function openSheet(sheet) {
    sheet.showModal();
  }

  // A tap on the backdrop lands on the dialog element itself rather than the panel
  // inside it, which is what distinguishes the two. Registered once per sheet: doing it
  // per-open with `once` meant the first tap *inside* the panel silently consumed the
  // listener, leaving no way out on a phone with no back gesture.
  [el.pairSheet, el.sourceSheet].forEach((sheet) => {
    sheet.addEventListener('click', (event) => {
      if (event.target === sheet) sheet.close();
    });
  });

  el.source.addEventListener('click', () => { if (!el.source.disabled) openSheet(el.sourceSheet); });

  el.pair.addEventListener('click', async () => {
    openSheet(el.pairSheet);
    try {
      const info = await (await fetch('/api/pair', { credentials: 'same-origin' })).json();
      el.qrHolder.innerHTML = info.qr || '';
      el.pairUrl.textContent = info.url || '';
      el.copyUrl.dataset.url = info.url || '';
      el.altUrls.replaceChildren();
      if ((info.alternatives || []).length) {
        const note = document.createElement('p');
        note.className = 'hint';
        note.style.margin = '14px 0 8px';
        note.textContent = 'If that address does not work, try:';
        el.altUrls.append(note);
        info.alternatives.forEach((entry) => {
          const url = typeof entry === 'string' ? entry : entry.url;
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'address';
          button.style.marginTop = '6px';
          const span = document.createElement('span');
          span.textContent = url;
          button.append(span);
          if (entry && entry.label && entry.label !== url) {
            const tag = document.createElement('em');
            tag.className = 'adapter';
            tag.textContent = entry.label;
            button.append(tag);
          }
          button.addEventListener('click', () => copy(url));
          el.altUrls.append(button);
        });
      }
    } catch (_) {
      el.pairUrl.textContent = 'Could not build a pairing link';
    }
  });

  async function copy(text) {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      toast('Link copied');
    } catch (_) {
      // Clipboard access needs a secure context, which plain http on a LAN is not.
      toast('Copy it from the address above');
    }
  }
  el.copyUrl.addEventListener('click', () => copy(el.copyUrl.dataset.url));

  /* ------------------------------------------------------------- lock screen --
   * A web page only gets a spot on the phone's lock screen while it is "playing"
   * something, so this loops ten seconds of digital silence and paints the PC's track
   * over it through the Media Session API: artwork, title, transport buttons and the
   * scrubber all appear where a local player's would. Opt-in, because starting the
   * silent track takes the phone's audio focus -- it will pause anything the phone
   * itself is playing -- and because autoplay rules require a tap to start it anyway.
   */

  const lockscreen = (() => {
    const supported = 'mediaSession' in navigator && typeof MediaMetadata === 'function';
    let enabled = false;
    let audio = null;
    let lastSignature = '';

    const remember = (on) => {
      try {
        if (on) localStorage.setItem('werv_lockscreen', '1');
        else localStorage.removeItem('werv_lockscreen');
      } catch (_) { /* private mode: the preference just does not stick */ }
    };
    const remembered = () => {
      try { return localStorage.getItem('werv_lockscreen') === '1'; } catch (_) { return false; }
    };

    function anchor() {
      if (!audio) {
        audio = document.createElement('audio');
        audio.id = 'anchor';
        audio.loop = true;
        audio.preload = 'none';        // nothing is fetched until the feature is used
        audio.src = '/silence.wav';
        audio.hidden = true;
        document.body.append(audio);
      }
      return audio;
    }

    function act(name, handler) {
      // Unknown action names throw rather than no-op, and the supported set differs
      // by platform and version.
      try { navigator.mediaSession.setActionHandler(name, handler); } catch (_) { /* not offered here */ }
    }

    function register() {
      // Explicit play/pause when the app advertises them; its toggle otherwise. The
      // lock screen only shows one of the two at a time, so toggle is a safe stand-in.
      act('play', () => { anchor().play().catch(() => {}); send(state && state.can.play ? 'play' : 'toggle'); });
      act('pause', () => { anchor().pause(); send(state && state.can.pause ? 'pause' : 'toggle'); });
      act('previoustrack', () => send('previous'));
      act('nexttrack', () => send('next'));
      act('seekto', (details) => {
        if (state && state.timeline.seekable && typeof details.seekTime === 'number') {
          send('seek', { seconds: details.seekTime });
        }
      });
    }

    function unregister() {
      ['play', 'pause', 'previoustrack', 'nexttrack', 'seekto'].forEach((name) => act(name, null));
    }

    function sync(next) {
      if (!enabled || !supported) return;
      const session = navigator.mediaSession;
      if (!next || next.status === 'idle' || next.status === 'unavailable') {
        // Nothing to control: a lock-screen tile for silence would just be confusing.
        session.metadata = null;
        session.playbackState = 'none';
        if (audio) audio.pause();
        lastSignature = '';
        return;
      }

      const track = next.track;
      const signature = `${track.id}|${track.art || ''}`;
      if (signature !== lastSignature) {
        lastSignature = signature;
        session.metadata = new MediaMetadata({
          title: track.title || 'Unknown track',
          artist: track.artist || '',
          album: track.album || '',
          artwork: track.art
            ? [{ src: new URL(track.art, location.href).href, sizes: '640x640', type: 'image/jpeg' }]
            : [],
        });
      }

      const playing = next.status === 'playing';
      session.playbackState = playing ? 'playing' : 'paused';
      // Mirror the anchor: iOS shows the *element's* state, not the declared one, so a
      // remote pause has to actually pause the silence for the lock screen to agree.
      if (playing) anchor().play().catch(() => { /* needs a fresh gesture; the button re-arms */ });
      else if (audio) audio.pause();

      const { duration, rate } = next.timeline;
      if (duration > 0 && isFinite(duration)) {
        try {
          session.setPositionState({
            duration,
            playbackRate: rate || 1,
            position: clamp(livePosition(), 0, duration),
          });
        } catch (_) { /* a UA that dislikes the numbers keeps its own estimate */ }
      }
    }

    async function enable({ quiet = false } = {}) {
      if (!supported || enabled) return enabled;
      try {
        await anchor().play();
      } catch (_) {
        // Autoplay rules want a user gesture; the next tap anywhere will retry.
        if (!quiet) toast('Tap the phone button again to allow it');
        armRestore();
        return false;
      }
      enabled = true;
      remember(true);
      register();
      el.lockscreen.setAttribute('aria-pressed', 'true');
      sync(state);
      if (!quiet) toast('Controls will show on the lock screen');
      return true;
    }

    function disable() {
      enabled = false;
      remember(false);
      unregister();
      if (audio) audio.pause();
      navigator.mediaSession.metadata = null;
      navigator.mediaSession.playbackState = 'none';
      lastSignature = '';
      el.lockscreen.setAttribute('aria-pressed', 'false');
    }

    function armRestore() {
      document.addEventListener('click', () => { enable({ quiet: true }); }, { once: true });
    }

    if (supported) {
      el.lockscreen.hidden = false;
      el.lockscreen.addEventListener('click', () => {
        buzz();
        if (enabled) { disable(); toast('Lock screen player off'); }
        else enable();
      });
      if (remembered()) armRestore();
    }

    return { sync };
  })();

  /* -------------------------------------------------------------- keyboard --- */

  document.addEventListener('keydown', (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    if (document.activeElement && document.activeElement.classList.contains('rail')) return;

    switch (event.key) {
      case ' ': case 'k': event.preventDefault(); el.toggle.click(); break;
      case 'ArrowRight': case 'l':
        event.preventDefault();
        if (seekable()) send('seek', { seconds: livePosition() + 10 }); else el.next.click();
        break;
      case 'ArrowLeft': case 'j':
        event.preventDefault();
        if (seekable()) send('seek', { seconds: Math.max(0, livePosition() - 10) }); else el.prev.click();
        break;
      case 'ArrowUp':
        if (state && state.volume.available) {
          event.preventDefault(); send('set_volume', { level: clamp(state.volume.level + 0.05, 0, 1) });
        }
        break;
      case 'ArrowDown':
        if (state && state.volume.available) {
          event.preventDefault(); send('set_volume', { level: clamp(state.volume.level - 0.05, 0, 1) });
        }
        break;
      case 'm': el.mute.click(); break;
      case 'n': el.next.click(); break;
      case 'p': el.prev.click(); break;
      case 's': el.shuffle.click(); break;
      case 'r': el.repeat.click(); break;
      default: break;
    }
  });

  /* ------------------------------------------------------------------- boot -- */

  refresh();
  connect();
  schedule();
})();
