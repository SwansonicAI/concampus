// The Briefbogen editor (setup/paper.html): an A4 page in mm from the top left, as core/paper.py sets it, its elements
// placed by hand, under rulers; or the PDF it makes (Vorschau). Drag an element to move it, a corner to size it; it
// snaps to half millimetres and to the page's middle and the edges of the others (Alt: freely), its place and size
// showing as it goes. Arrow keys nudge (Shift: 5 mm), Delete removes, Ctrl+D doubles, Ctrl+Z and Ctrl+Y undo and
// redo, Escape lets go; a double click on a text edits it. What is held fast („Festhalten“) lets clicks through to
// what lies beneath, as a frame round the page; the list of elements still picks it. The rail at the window's edge
// saves, adds, and edits what is picked, else the Briefbogen: its type, its fonts and pictures. Texts show the
// Hochschule's own values for their placeholders, a line whose placeholders are all empty left out, as on paper.
(() => {
  'use strict';
  const root = document.getElementById('paper');
  if (!root) return;
  const data = JSON.parse(document.getElementById('paper-data').textContent);
  const desk = root.querySelector('.paper-desk'), board = root.querySelector('.paper-board'), sheet = root.querySelector('.paper-sheet');
  const panel = root.querySelector('.paper-panel'), frame = root.querySelector('.paper-frame'), sample = root.querySelector('.paper-sample');
  const rulers = {top: root.querySelector('.paper-ruler.top'), left: root.querySelector('.paper-ruler.left')};
  const saveForm = document.getElementById('paper-save'), previewForm = document.getElementById('paper-preview');
  const csrf = saveForm.querySelector('[name=csrfmiddlewaretoken]').value;
  const W = 210, H = 297, S = 8, PT = 25.4 / 72, SNAP = 0.5, CATCH = 1.5, LEADING = 1.25, RULER = 20, GUTTER = 28, REAL = 96 / 25.4;
  const COLOURS = {accent: ['Akzent', data.accent], ink: ['Schwarz', '#000000'], muted: ['Grau', '#5f5f5f'], line: ['Hellgrau', '#d6d6d6'], white: ['Weiß', '#ffffff']};
  const NAMES = {body: 'Textbereich', text: 'Text', wordmark: 'Schriftzug', picture: 'Bild', box: 'Fläche', address: 'Anschriftfeld', info: 'Infoblock'};
  const PAGES = {first: 'erste Seite', later: 'Folgeseiten', all: 'alle Seiten'};
  const FACES = {regular: 'Normal', bold: 'Fett', italic: 'Kursiv', bolditalic: 'Fett kursiv'};
  const ALIGN = [['left', 'Links'], ['center', 'Mitte'], ['right', 'Rechts']];
  const ADDRESS = ['Erika Mustermann', 'Musterstraße 1', '12345 Musterstadt'];
  const GRIPS = ['nw', 'ne', 'sw', 'se'].map(c => `<i class="paper-grip" data-corner="${c}"></i>`).join('');

  let design = data.design, name = data.name, variant = 'first', picked = null, k = 1;
  let zoom = localStorage.getItem('paper-zoom') || 'width';
  let undos = [], redos = [], was = JSON.stringify([design, name]), typed = null, note = null;  // note: {text, error} over the panel

  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const round = v => Math.round(v * 100) / 100;
  const snap = v => Math.round(v / SNAP) * SNAP;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const mm = v => String(round(v)).replace('.', ',');
  const colour = v => COLOURS[v]?.[1] || v;
  const family = key => `'paper-${data.fonts[key] ? key : 'ptsans'}', sans-serif`;  // single quotes: it stands in a style="" attribute
  const uid = () => Math.random().toString(36).slice(2, 8);
  const named = el => el.type !== 'box' ? NAMES[el.type] : Math.min(el.w, el.h) <= 1 ? 'Linie' : el.fill ? 'Fläche' : 'Rahmen';
  const shown = el => el.pages === 'all' || el.pages === variant;
  const byId = id => design.elements.find(el => el.id === id);
  const nodeOf = id => sheet.querySelector(`.paper-el[data-id="${CSS.escape(id)}"]`);
  const px = pt => pt * PT * S;  // points on paper, pixels on the page as laid out
  const typing = () => document.activeElement?.matches('input, textarea, select');

  // ---- changes: each one an undo step; typing into one field is one step
  function changed(field = null) {
    const now = JSON.stringify([design, name]);
    if (now === was) return;
    if (!field || field !== typed) {
      undos.push(was);
      if (undos.length > 100) undos.shift();
    }
    redos = [];
    typed = field;
    was = now;
    saveForm.dataset.dirty = '1';  // leaving asks first (app.js)
  }
  function restore(from, to) {
    if (!from.length) return;
    to.push(was);
    was = from.pop();
    [design, name] = JSON.parse(was);
    typed = null;
    if (picked && !byId(picked)) picked = null;
    saveForm.dataset.dirty = '1';
    draw();
    inspect();
  }

  // ---- placeholders, as core/paper.py filled() fills them
  function filled(text, values) {
    const out = [];
    for (const line of String(text).split('\n')) {
      const names = [...line.matchAll(/\{(\w+)\}/g)].map(m => m[1]);
      if (names.length && !names.some(n => values[n])) continue;
      out.push(...line.replace(/\{(\w+)\}/g, (m, n) => n in values ? values[n] : m).split('\n'));
    }
    return out;
  }
  const markup = (words, caps) => esc(caps ? words.toUpperCase() : words).replace(/\*\*(.+?)\*\*/gs, '<b>$1</b>');  // bold may span lines
  const smallCaps = line => [...line].map(ch => ch !== ch.toUpperCase() ? `<span>${esc(ch.toUpperCase())}</span>` : esc(ch)).join('');

  // ---- the page's size: the whole page, the desk's width, real size or twice it
  function measure() {
    const room = [desk.clientWidth - RULER - 2 * GUTTER, desk.clientHeight - RULER - 2 * GUTTER];
    return {page: Math.min(room[0] / W, room[1] / H), width: room[0] / W, 100: REAL, 200: 2 * REAL}[zoom] || room[0] / W;
  }
  root.querySelector('.paper-zoom').addEventListener('click', e => {
    const z = e.target.closest('[data-zoom]')?.dataset.zoom;
    if (!z) return;
    zoom = z;
    localStorage.setItem('paper-zoom', z);
    draw();
  });
  new ResizeObserver(() => variant !== 'pdf' && Math.abs(measure() - k) > 0.001 && draw()).observe(desk);

  // ---- rulers: mm from the page's corner, ticks as dense as they stay apart; what is picked marked on them
  function ruler(canvas, length, across) {
    const ratio = devicePixelRatio || 1, size = length * k + 2 * GUTTER;
    canvas.width = (across ? RULER : size) * ratio;
    canvas.height = (across ? size : RULER) * ratio;
    canvas.style.width = `${across ? RULER : size}px`;
    canvas.style.height = `${across ? size : RULER}px`;
    const c = canvas.getContext('2d');
    c.scale(ratio, ratio);
    c.fillStyle = '#f2f2f2';
    c.fillRect(0, 0, across ? RULER : size, across ? size : RULER);
    const el = picked && byId(picked);
    if (el && shown(el)) {
      const [a, s] = across ? [el.y, el.h] : [el.x, el.w];
      c.fillStyle = '#e5f3e5';
      across ? c.fillRect(0, GUTTER + a * k, RULER, Math.max(1, s * k)) : c.fillRect(GUTTER + a * k, 0, Math.max(1, s * k), RULER);
    }
    const label = k * 10 >= 26 ? 10 : k * 20 >= 26 ? 20 : 50, minor = k >= 4 ? 1 : k * 5 >= 4 ? 5 : 10;
    c.strokeStyle = '#8a8a8a';
    c.fillStyle = '#5f5f5f';
    c.font = '10px system-ui, sans-serif';
    c.lineWidth = 1;
    c.beginPath();
    for (let v = 0; v <= length; v += minor) {
      const at = Math.round(GUTTER + v * k) + 0.5, long = v % label === 0 ? 8 : v % 5 === 0 ? 5 : 3;
      across ? (c.moveTo(RULER - long, at), c.lineTo(RULER, at)) : (c.moveTo(at, RULER - long), c.lineTo(at, RULER));
      if (v % label === 0) {
        if (across) {
          c.save();
          c.translate(9, at + 3);
          c.rotate(-Math.PI / 2);
          c.fillText(v, 0, 0);
          c.restore();
        } else c.fillText(v, at + 3, 10);
      }
    }
    c.stroke();
  }
  const marked = () => { ruler(rulers.top, W, false); ruler(rulers.left, H, true); };

  // ---- drawing. The page is laid out at S px a mm and drawn at its size as a whole: no text falls under the
  // browser's least font size, and every length stays in mm times S.
  function draw() {
    root.querySelectorAll('[data-variant]').forEach(b => b.classList.toggle('on', b.dataset.variant === variant));
    root.querySelectorAll('[data-zoom]').forEach(b => b.classList.toggle('on', b.dataset.zoom === zoom));
    const pdf = variant === 'pdf';
    board.hidden = pdf;
    frame.hidden = !pdf;
    sample.hidden = !pdf;
    root.querySelector('.paper-zoom').hidden = pdf;
    root.querySelectorAll('[data-add]').forEach(b => { b.disabled = pdf || (b.dataset.add === 'address' || b.dataset.add === 'info') && design.elements.some(el => el.type === b.dataset.add); });
    if (pdf) return;
    k = measure();
    Object.assign(sheet.style, {width: `${W * k}px`, height: `${H * k}px`});
    const first = variant === 'first';
    const guides = (first && data.letters ? `<div class="paper-window" style="${place({x: 20, y: 45, w: 85, h: 45})}"><small>Fenster DIN 5008</small></div>` : '') +
      (first && design.style.marks ? [105, 148.5, 210].map((y, n) => `<i class="paper-mark" style="left:${4 * S}px;top:${y * S}px;width:${(n === 1 ? 6 : 3.5) * S}px"></i>`).join('') : '');
    sheet.innerHTML = `<div class="paper-page" style="width:${W * S}px;height:${H * S}px;transform:scale(${k / S});--fit:${k / S}">` + guides +
      design.elements.filter(shown).map(element).join('') + '<i class="paper-guide v" hidden></i><i class="paper-guide h" hidden></i><span class="paper-size" hidden></span></div>';
    marked();
  }
  const place = el => `left:${el.x * S}px;top:${el.y * S}px;width:${el.w * S}px;height:${el.h * S}px`;
  function element(el) {
    const st = design.style, font = el.font || st.font;
    let inner = '', css = place(el);
    if (el.type === 'text') {
      const lines = filled(el.text, data.values);
      inner = `<div class="paper-lines">${!lines.some(l => l.trim()) ? `<span class="paper-raw">${esc(el.text)}</span>` : markup(lines.join('\n'), el.caps)}</div>`;
      css += `;font-family:${family(font)};font-size:${px(el.size)}px;line-height:${el.leading || LEADING};color:${colour(el.colour)};text-align:${el.align || 'left'};` +
        `font-weight:${el.bold ? 700 : 400};font-style:${el.italic ? 'italic' : 'normal'};text-decoration:${el.underline ? 'underline' : 'none'}`;
    } else if (el.type === 'wordmark') {
      if (data.logo) inner = `<img src="${esc(data.logo)}" alt="" style="object-position:${{center: 'center', right: 'right'}[el.align] || 'left'} top">`;
      else inner = data.wordmark.map((line, n) => n ? `<div class="paper-caps" style="color:${data.accent}">${smallCaps(line)}</div>` : `<div>${esc(line)}</div>`).join('');
      css += `;font-family:${family(st.font)};font-size:${px(el.size)}px;line-height:1.15;text-align:${el.align}`;
    } else if (el.type === 'picture') {
      const pic = data.pictures.find(p => p.pk === el.artwork);
      inner = pic ? `<img src="${esc(pic.url)}" alt="">` : '<small class="paper-raw">Bild fehlt</small>';
    } else if (el.type === 'box') {
      css += el.fill ? `;background:${colour(el.colour)}` : `;border:${Math.max(S * 0.15, px(el.line))}px solid ${colour(el.colour)}`;
      if (!el.fill) inner = '<i class="paper-edge n"></i><i class="paper-edge e"></i><i class="paper-edge s"></i><i class="paper-edge w"></i>';  // a frame is grabbed by its edges
    } else if (el.type === 'address') {
      inner = ADDRESS.map(l => `<div>${esc(l)}</div>`).join('');
      css += `;font-family:${family(st.font)};font-size:${px(el.size)}px;line-height:1.3`;
    } else if (el.type === 'info') {
      const v = data.values;
      inner = `<div><b>${esc(v.amt)}</b></div><dl><dt>Aktenzeichen</dt><dd>${esc(v.aktenzeichen)}</dd><dt>Datum</dt><dd>${esc(v.datum)}</dd></dl>`;
      css += `;font-family:${family(st.font)};font-size:${px(el.size)}px`;
    } else if (el.type === 'body') {
      const title = variant === 'first' ? `${st.bar ? `<i class="paper-bar" style="background:${data.accent}"></i>` : ''}<div class="paper-title" style="font-family:${family(st.heading)};font-size:${px(st.title)}px">Titel des Dokuments</div>` : '';
      const words = 'Hier steht der Text des Dokuments, in seiner Schrift und Größe. Er fließt in diesen Bereich und auf die Folgeseiten, so lang er ist.';
      inner = `<span class="paper-label">Textbereich</span><div class="paper-flow${st.middle ? ' middle' : ''}${st.align === 'center' ? ' centred' : ''}" style="text-align:${st.align};font-family:${family(st.font)};font-size:${px(st.size)}px">${title}<p>${words}</p><p>${words}</p></div>`;
    }
    const kind = `paper-el paper-${el.type}${el.type === 'box' && !el.fill ? ' frame' : ''}${el.id === picked ? ' on' : ''}${el.locked ? ' locked' : ''}`;
    return `<div class="${kind}" data-id="${esc(el.id)}" data-name="${named(el)}" style="${css}">${inner}${el.id === picked && !el.locked ? GRIPS : ''}</div>`;
  }

  function pick(id) {
    picked = id;
    sheet.querySelectorAll('.paper-el').forEach(node => {
      const on = node.dataset.id === id;
      node.classList.toggle('on', on);
      node.querySelectorAll('.paper-grip').forEach(g => g.remove());
      if (on && !byId(id).locked) node.insertAdjacentHTML('beforeend', GRIPS);
    });
    marked();
    inspect();
  }

  // ---- moving and sizing, snapping to the page's middle and the other elements' edges
  function lines(el, axis) {
    const found = axis === 'x' ? [0, W / 2, W] : [0, H / 2, H];
    design.elements.filter(o => shown(o) && o !== el).forEach(o => {
      const [a, s] = axis === 'x' ? [o.x, o.w] : [o.y, o.h];
      found.push(a, a + s / 2, a + s);
    });
    return found;
  }
  // `pos` moved, its edges at `offsets` from it each trying the nearest line within CATCH: [pos, the line or null].
  function align(pos, offsets, list, free) {
    if (free) return [round(pos), null];
    let best = null;
    for (const off of offsets) for (const line of list) {
      const d = line - (pos + off);
      if (Math.abs(d) <= CATCH && (!best || Math.abs(d) < Math.abs(best[0]))) best = [d, line];
    }
    return best ? [pos + best[0], best[1]] : [snap(pos), null];
  }
  function guide(axis, line) {
    const g = sheet.querySelector(`.paper-guide.${axis === 'x' ? 'v' : 'h'}`);
    g.hidden = line == null;
    if (line != null) g.style[axis === 'x' ? 'left' : 'top'] = `${line * S}px`;
  }
  function measured(el) {  // where it is and how large, under it while it moves
    const badge = sheet.querySelector('.paper-size');
    badge.hidden = !el;
    if (!el) return;
    badge.textContent = `${mm(el.x)} · ${mm(el.y)}   ${mm(el.w)} × ${mm(el.h)} mm`;
    Object.assign(badge.style, {left: `${el.x * S}px`, top: `${(el.y + el.h) * S}px`});
  }
  sheet.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    const node = e.target.closest('.paper-el');
    sheet.focus({preventScroll: true});
    if (!node) return picked && pick(null);
    const el = byId(node.dataset.id), corner = e.target.dataset.corner, from = {...el}, x0 = e.clientX, y0 = e.clientY;
    if (el.id !== picked) pick(el.id);
    e.preventDefault();
    sheet.setPointerCapture(e.pointerId);
    const least = el.type === 'body' ? 40 : 1, thin = el.type === 'box' ? 0.1 : least, xs = lines(el, 'x'), ys = lines(el, 'y'), target = nodeOf(el.id);
    let moved = false;
    const move = ev => {
      const dx = (ev.clientX - x0) / k, dy = (ev.clientY - y0) / k, free = ev.altKey;
      if (!moved && Math.hypot(dx, dy) * k < 3) return;  // a click, not a drag
      moved = true;
      let gx = null, gy = null;
      if (!corner) {
        [el.x, gx] = align(from.x + dx, [0, from.w / 2, from.w], xs, free);
        [el.y, gy] = align(from.y + dy, [0, from.h / 2, from.h], ys, free);
        el.x = round(clamp(el.x, -5, W - 1));
        el.y = round(clamp(el.y, -5, H - 1));
      } else {
        let edge;
        if (corner.includes('w')) {
          [edge, gx] = align(from.x + dx, [0], xs, free);
          edge = Math.min(edge, from.x + from.w - least);
          [el.x, el.w] = [round(edge), round(from.x + from.w - edge)];
        } else {
          [edge, gx] = align(from.x + from.w + dx, [0], xs, free);
          el.w = round(Math.max(least, edge - from.x));
        }
        if (corner.includes('n')) {
          [edge, gy] = align(from.y + dy, [0], ys, free);
          edge = Math.min(edge, from.y + from.h - thin);
          [el.y, el.h] = [round(edge), round(from.y + from.h - edge)];
        } else {
          [edge, gy] = align(from.y + from.h + dy, [0], ys, free);
          el.h = round(Math.max(thin, edge - from.y));
        }
      }
      Object.assign(target.style, {left: `${el.x * S}px`, top: `${el.y * S}px`, width: `${el.w * S}px`, height: `${el.h * S}px`});
      guide('x', gx);
      guide('y', gy);
      measured(el);
      fields();
    };
    const up = () => {
      sheet.removeEventListener('pointermove', move);
      sheet.removeEventListener('pointerup', up);
      sheet.removeEventListener('pointercancel', up);
      if (!moved) return;
      changed();
      draw();
    };
    sheet.addEventListener('pointermove', move);
    sheet.addEventListener('pointerup', up);
    sheet.addEventListener('pointercancel', up);
  });
  sheet.addEventListener('dblclick', e => {  // a text: its words, to type
    const el = byId(e.target.closest('.paper-el')?.dataset.id);
    if (el?.type !== 'text') return;
    const area = panel.querySelector('textarea');
    area?.focus();
    area?.select();
  });

  // ---- keys: anywhere on the page but in a field
  document.addEventListener('keydown', e => {
    if (typing() || e.target.closest?.('.drawer, dialog') || variant === 'pdf') return;
    const key = (e.key || '').toLowerCase(), mod = e.ctrlKey || e.metaKey;
    if (mod && (key === 'z' || key === 'y')) {
      e.preventDefault();
      return key === 'z' && !e.shiftKey ? restore(undos, redos) : restore(redos, undos);
    }
    const el = picked && byId(picked);
    if (!el) return;
    if (key === 'escape') return pick(null);
    if (mod && key === 'd') {
      e.preventDefault();
      return duplicate(el);
    }
    if ((key === 'delete' || key === 'backspace') && el.type !== 'body') {
      e.preventDefault();
      return remove(el);
    }
    const step = e.shiftKey ? 5 : SNAP, by = {arrowleft: [-step, 0], arrowright: [step, 0], arrowup: [0, -step], arrowdown: [0, step]}[key];
    if (by && !el.locked) {
      e.preventDefault();
      el.x = round(clamp(el.x + by[0], -5, W - 1));
      el.y = round(clamp(el.y + by[1], -5, H - 1));
      changed(`nudge:${el.id}`);
      draw();
      fields();
    }
  });

  // ---- adding, doubling, removing, layering
  const NEW = {
    text: () => ({type: 'text', w: 80, h: 8, text: 'Neuer Text', size: 10, leading: LEADING, colour: 'ink', align: 'left', font: '', bold: false, italic: false, underline: false, caps: false}),
    wordmark: () => ({type: 'wordmark', w: 100, h: 17, size: 15, align: 'left'}),
    picture: () => ({type: 'picture', w: 40, h: 40}),
    box: () => ({type: 'box', w: 60, h: 30, colour: 'accent', fill: true, line: 0.5}),
    rule: () => ({type: 'box', w: 165, h: 0.3, colour: 'ink', fill: true, line: 0.5}),
    address: () => ({type: 'address', x: 25, y: 63.5, w: 80, h: 26.5, size: 10, pages: 'first'}),
    info: () => ({type: 'info', x: 125, y: 47, w: 65, h: 40, size: 8.5, pages: 'first'}),
  };
  function add(kind, extra = {}) {
    const el = {id: uid(), x: 25, pages: variant, ...NEW[kind](), ...extra};
    if (el.y == null) {  // in the middle of what of the page shows
      const r = sheet.getBoundingClientRect(), d = desk.getBoundingClientRect();
      const top = Math.max(0, d.top - r.top), bottom = Math.min(r.height, d.bottom - r.top);
      el.y = snap(clamp((top + bottom) / 2 / k - el.h / 2, 0, H - el.h));
    }
    design.elements.push(el);
    changed();
    draw();
    pick(el.id);
    if (kind === 'text') panel.querySelector('textarea')?.select();
  }
  root.querySelector('.paper-add').addEventListener('click', e => {
    const kind = e.target.closest('[data-add]')?.dataset.add;
    if (!kind) return;
    if (kind !== 'picture') return add(kind);
    if (data.pictures.length) return add('picture', sized(data.pictures.at(-1)));
    choose('picture').then(pic => pic && add('picture', sized(pic)));
  });
  const sized = pic => ({artwork: pic.pk, w: 40, h: round(pic.w && pic.h ? 40 * pic.h / pic.w : 40)});
  function duplicate(el) {
    if (['body', 'address', 'info'].includes(el.type)) return;
    const copy = {...structuredClone(el), id: uid(), x: round(el.x + 5), y: round(el.y + 5), locked: false};
    design.elements.push(copy);
    changed();
    draw();
    pick(copy.id);
  }
  function remove(el) {
    design.elements = design.elements.filter(o => o !== el);
    picked = null;
    changed();
    draw();
    inspect();
  }
  function layer(el, front) {
    design.elements = design.elements.filter(o => o !== el);
    front ? design.elements.push(el) : design.elements.unshift(el);
    changed();
    draw();
    inspect();
  }

  // ---- uploads (setup.papers.artworks): a picture, or a face of the Hochschule's own font
  async function upload(kind, file, extra = {}) {
    const form = new FormData();
    form.append('art', kind);
    form.append('datei', file);
    Object.entries(extra).forEach(([key, value]) => form.append(key, value));
    const r = await fetch(data.upload, {method: 'POST', body: form, headers: {'X-CSRFToken': csrf}, credentials: 'same-origin'}).catch(() => null);
    const d = r ? await r.json().catch(() => ({})) : {};
    if (!r?.ok) throw new Error(d.error || 'Hochladen fehlgeschlagen. Bitte die Verbindung prüfen.');
    return d;
  }
  function choose(kind, extra = {}) {
    return new Promise(resolve => {
      const input = Object.assign(document.createElement('input'), {type: 'file', accept: kind === 'font' ? '.ttf,font/ttf' : 'image/png,image/jpeg'});
      input.addEventListener('change', async () => {
        if (!input.files[0]) return resolve(null);
        note = {text: 'Lädt hoch …'};
        inspect();
        try {
          const d = await upload(kind, input.files[0], extra);
          note = null;
          if (kind === 'picture') data.pictures.push({pk: d.pk, name: d.name, url: d.url, w: d.w, h: d.h});
          else await learn(d);
          resolve(d);
        } catch (error) {
          note = {text: error.message, error: true};
          resolve(null);
        }
        inspect();
      });
      input.click();
    });
  }
  async function learn(d) {  // a face uploaded: known to the page's fonts and to the editor's
    const f = data.fonts[d.key] ||= {name: d.name, own: true, faces: {}, files: []};
    f.faces[d.face] = d.url;
    f.files = [...f.files.filter(x => x.face !== d.face), {pk: d.pk, face: d.face}];
    const face = new FontFace(`paper-${d.key}`, `url("${d.url}")`, {weight: d.face.includes('bold') ? '700' : '400', style: d.face.includes('italic') ? 'italic' : 'normal'});
    document.fonts.add(await face.load().catch(() => face));
    draw();
  }
  async function discard(pk, kind) {
    const r = await fetch(`${data.upload}${pk}/loeschen/`, {method: 'POST', headers: {'X-CSRFToken': csrf}, credentials: 'same-origin'}).catch(() => null);
    const d = r ? await r.json().catch(() => ({})) : {};
    if (!r?.ok) {
      note = {text: d.error || 'Nicht gelöscht.', error: true};
      return inspect();
    }
    note = null;
    if (kind === 'picture') data.pictures = data.pictures.filter(p => p.pk !== pk);
    else for (const [key, f] of Object.entries(data.fonts)) {
      f.files = (f.files || []).filter(x => x.pk !== pk);
      if (f.own && !f.files.length) delete data.fonts[key];
    }
    draw();
    inspect();
  }

  // ---- the panel: what is picked, else the Briefbogen
  const field = (label, html, narrow = false) => `<div${narrow ? ' class="paper-narrow"' : ''}><label>${label}</label>${html}</div>`;
  const number = (key, value, step, min, max) => `<input type="number" data-key="${key}" value="${value ?? ''}" step="${step}" min="${min}" max="${max}">`;
  const check = (key, label, on) => `<label class="option"><input type="checkbox" data-key="${key}"${on ? ' checked' : ''}> ${label}</label>`;
  const options = (pairs, value) => pairs.map(([v, l]) => `<option value="${esc(v)}"${v === value ? ' selected' : ''}>${esc(l)}</option>`).join('');
  const fontPick = (key, value, inherit) => `<select data-key="${key}">${inherit ? '<option value="">Wie der Text</option>' : ''}${options(Object.entries(data.fonts).map(([v, f]) => [v, f.name]), value)}</select>`;
  const segments = (key, pairs, value) => `<div class="paper-seg">${pairs.map(([v, l]) => `<button type="button" data-key="${key}" data-value="${v}"${v === value ? ' class="on"' : ''}>${l}</button>`).join('')}</div>`;
  const swatches = (key, value) => `<div class="paper-swatches">${Object.entries(COLOURS).map(([v, [l, c]]) =>
    `<button type="button" data-key="${key}" data-value="${v}" title="${l}" style="background:${c}"${v === value ? ' class="on"' : ''}></button>`).join('')}` +
    `<input type="color" data-key="${key}" value="${/^#/.test(value) ? value : colour(value)}" title="Eigene Farbe"></div>`;
  const section = (title, html) => `<section class="paper-section"><h4>${title}</h4>${html}</section>`;

  function inspect() {
    const el = picked && byId(picked);
    panel.innerHTML = (note ? `<p class="flash${note.error ? ' error' : ''}">${esc(note.text)}</p>` : '') + (el ? elementPanel(el) : layoutPanel());
  }
  function elementPanel(el) {
    let html = `<p class="paper-crumb"><button type="button" class="link" data-act="none"><i class="arrow left"></i> Briefbogen</button></p><h3>${named(el)}</h3>`;
    if (el.type === 'text') {
      html += field('Text', `<textarea data-key="text" rows="4" spellcheck="false">${esc(el.text)}</textarea>`) +
        `<p class="chips paper-fields">${Object.entries(data.fields).map(([f, about]) => `<button type="button" class="chip" data-field="${f}" title="${esc(about)}">{${f}}</button>`).join('')}</p>` +
        '<p class="muted paper-hint"><small>Ein Klick setzt den Platzhalter ein. **so** steht fett. Eine Zeile, deren Platzhalter alle leer sind, entfällt.</small></p>' +
        section('Schrift', field('Schrift', fontPick('font', el.font || '', true)) +
          `<div class="row">${field('Größe (pt)', number('size', el.size, 0.5, 4, 72), true)}${field('Zeilenabstand', number('leading', el.leading ?? LEADING, 0.05, 0.8, 3), true)}</div>` +
          `<div class="paper-checks">${check('bold', 'Fett', el.bold)}${check('italic', 'Kursiv', el.italic)}${check('underline', 'Unterstrichen', el.underline)}${check('caps', 'Großbuchstaben', el.caps)}</div>` +
          field('Farbe', swatches('colour', el.colour)) + field('Ausrichtung', segments('align', ALIGN, el.align || 'left')));
    } else if (el.type === 'wordmark') {
      html += (data.logo ? `<p class="muted"><small>Das Logo der Hochschule, so groß der Rahmen es fasst.${data.svg ? ' Als SVG steht im PDF der Schriftzug; für das PDF ein PNG hochladen (Einstellungen › Hochschule).' : ''}</small></p>` :
        '<p class="muted"><small>Der Schriftzug der Hochschule (Einstellungen › Hochschule), in der Schrift des Texts.</small></p>') +
        `<div class="row">${field('Größe (pt)', number('size', el.size, 0.5, 6, 60), true)}</div>` + field('Ausrichtung', segments('align', ALIGN, el.align));
    } else if (el.type === 'picture') {
      html += pictures(el.artwork);
    } else if (el.type === 'box') {
      html += field('Farbe', swatches('colour', el.colour)) + field('Art', segments('fill', [['1', 'Fläche'], ['0', 'Rahmen']], el.fill ? '1' : '0')) +
        (el.fill ? '' : `<div class="row">${field('Linie (pt)', number('line', el.line, 0.1, 0.1, 10), true)}</div>`);
    } else if (el.type === 'address' || el.type === 'info') {
      html += `<p class="muted"><small>${el.type === 'address' ? 'Wem der Brief gilt, im Fenster des Umschlags: links 20, oben 45 mm, 85 × 45 mm nach DIN 5008.' : 'Das Amt, das Aktenzeichen und das Datum des Briefs.'}</small></p>` +
        `<div class="row">${field('Größe (pt)', number('size', el.size, 0.5, 6, 14), true)}</div>`;
    } else if (el.type === 'body') {
      html += `<p class="muted"><small>Hier fließt der Text des Dokuments, ${el.pages === 'first' ? 'auf der ersten Seite' : 'auf jeder weiteren'}. Schrift und Größe stehen beim Briefbogen.</small></p>`;
    }
    html += section('Lage und Größe in mm', `<div class="row">${['x', 'y', 'w', 'h'].map(a =>
      field({x: 'Links', y: 'Oben', w: 'Breite', h: 'Höhe'}[a], number(a, el[a], 0.5, a === 'w' || a === 'h' ? 0.1 : -5, a === 'x' || a === 'w' ? W : H), true)).join('')}</div>` +
      (el.type === 'body' ? '' : field('Auf', `<select data-key="pages">${options(Object.entries(PAGES), el.pages)}</select>`)) +
      `<span title="Nicht zu verschieben; ein Klick auf der Seite trifft, was darunter liegt. Wählen in der Liste der Elemente">${check('locked', 'Festhalten', el.locked)}</span>`);
    html += `<div class="actions paper-acts">${el.type === 'body' ? '' : '<button type="button" class="btn ghost small" data-act="front" title="Vor alles andere">Nach vorn</button><button type="button" class="btn ghost small" data-act="back" title="Hinter alles andere">Nach hinten</button>'}` +
      `${['body', 'address', 'info'].includes(el.type) ? '' : '<button type="button" class="btn ghost small" data-act="copy" title="Strg+D">Doppeln</button>'}` +
      `${el.type === 'body' ? '' : '<button type="button" class="link" data-act="remove" title="Entf">Entfernen</button>'}</div>`;
    return html;
  }
  function pictures(chosen) {
    return `<ul class="paper-pictures">${data.pictures.map(p => `<li${p.pk === chosen ? ' class="on"' : ''}><button type="button" data-picture="${p.pk}" title="${esc(p.name)}"><img src="${esc(p.url)}" alt="${esc(p.name)}"></button>` +
      `<button type="button" class="link" data-discard="${p.pk}" data-kind="picture" title="Löschen, wo kein Briefbogen es verwendet">×</button></li>`).join('')}</ul>` +
      `<p><button type="button" class="btn ghost small" data-upload="picture">Bild hochladen</button> <small class="muted">PNG oder JPEG, bis 5 MB</small></p>`;
  }
  function layoutPanel() {
    const st = design.style, own = Object.entries(data.fonts).filter(([, f]) => f.own);
    const list = design.elements.filter(shown).reverse().map(el => `<li><button type="button" class="link" data-pick="${esc(el.id)}">${named(el)}</button>` +
      `<small class="muted">${el.type === 'text' ? esc(el.text.split('\n')[0].slice(0, 28)) : ''}</small>` +
      `<label title="Festhalten"><input type="checkbox" data-lock="${esc(el.id)}"${el.locked ? ' checked' : ''}> fest</label></li>`).join('');
    return `<h3>Briefbogen</h3>` + field('Name', `<input data-key="name" value="${esc(name)}" maxlength="80">`) +
      section('Text des Dokuments', `<div class="row">${field('Schrift', fontPick('style.font', st.font))}${field('Überschriften', fontPick('style.heading', st.heading))}</div>` +
        `<div class="row">${field('Größe (pt)', number('style.size', st.size, 0.5, 7, 16), true)}${field('Titel (pt)', number('style.title', st.title, 1, 10, 48), true)}</div>` +
        `<div class="paper-checks">${check('style.bar', 'Balken über dem Titel', st.bar)}${check('style.center', 'Zentriert', st.align === 'center')}` +
        `${check('style.middle', 'Senkrecht in der Mitte', st.middle)}${check('style.marks', 'Falz- und Lochmarken', st.marks)}</div>`) +
      (variant === 'pdf' ? '' : section(`Elemente der ${variant === 'first' ? 'ersten Seite' : 'Folgeseiten'}, vorn zuerst`, `<ul class="paper-list">${list}</ul>`)) +
      section('Eigene Schriften', own.map(([, f]) => `<p><b>${esc(f.name)}</b> ${(f.files || []).map(x => `<span class="chip">${FACES[x.face]} <button type="button" class="link" data-discard="${x.pk}" data-kind="font" title="Löschen">×</button></span>`).join(' ')}</p>`).join('') +
        `<div class="row">${field('Familie', '<input data-font-name maxlength="80" placeholder="z. B. Hausschrift">')}${field('Schnitt', `<select data-font-face>${options(Object.entries(FACES), 'regular')}</select>`, true)}</div>` +
        '<p><button type="button" class="btn ghost small" data-upload="font">Schrift hochladen</button> <small class="muted">TrueType (.ttf), die das Einbetten erlaubt</small></p>') +
      section('Bilder', pictures(null));
  }
  // The visible fields follow what was dragged or nudged.
  function fields() {
    const el = picked && byId(picked);
    if (el) ['x', 'y', 'w', 'h'].forEach(a => { const input = panel.querySelector(`input[data-key="${a}"]`); if (input && document.activeElement !== input) input.value = el[a]; });
  }
  function set(key, value, input) {
    const el = picked && byId(picked);
    if (key === 'name') name = value.slice(0, 80);
    else if (key === 'style.center') design.style.align = value ? 'center' : 'left';
    else if (key.startsWith('style.')) design.style[key.slice(6)] = value;
    else if (!el) return;
    else if (key === 'fill') el.fill = value === '1';
    else el[key] = value;
    changed(input ? `${picked}:${key}` : null);
    draw();
    if (!input) inspect();
  }
  panel.addEventListener('input', e => {
    const t = e.target;
    if (t.dataset.lock) {
      byId(t.dataset.lock).locked = t.checked;
      changed();
      return draw();
    }
    const key = t.dataset.key;
    if (!key) return;
    let value = t.type === 'checkbox' ? t.checked : t.value;
    if (t.type === 'number') {
      value = parseFloat(t.value);
      if (Number.isNaN(value)) return;
      value = clamp(value, +t.min, +t.max);
    }
    set(key, value, t.type !== 'checkbox' && t.tagName !== 'SELECT');
  });
  panel.addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b) return;
    const el = picked && byId(picked);
    if (b.dataset.key) return set(b.dataset.key, b.dataset.value);
    if (b.dataset.field) {  // a placeholder, where the caret stands
      const area = panel.querySelector('textarea'), at = area.selectionStart ?? area.value.length, put = `{${b.dataset.field}}`;
      area.value = area.value.slice(0, at) + put + area.value.slice(area.selectionEnd ?? at);
      area.focus();
      area.selectionStart = area.selectionEnd = at + put.length;
      return set('text', area.value, true);
    }
    if (b.dataset.pick) return pick(b.dataset.pick);
    if (b.dataset.picture) return set('artwork', +b.dataset.picture);
    if (b.dataset.discard) return discard(+b.dataset.discard, b.dataset.kind);
    if (b.dataset.upload === 'picture') return choose('picture').then(pic => pic && el?.type === 'picture' && set('artwork', pic.pk));
    if (b.dataset.upload === 'font') {
      const name = panel.querySelector('[data-font-name]').value.trim(), face = panel.querySelector('[data-font-face]').value;
      if (!name) {
        note = {text: 'Bitte den Namen der Schrift angeben.', error: true};
        return inspect();
      }
      return choose('font', {name, schnitt: face});
    }
    if (b.dataset.act === 'none') return pick(null);
    if (!el) return;
    if (b.dataset.act === 'front' || b.dataset.act === 'back') return layer(el, b.dataset.act === 'front');
    if (b.dataset.act === 'copy') return duplicate(el);
    if (b.dataset.act === 'remove') return remove(el);
  });
  panel.addEventListener('focusout', () => { typed = null; });  // the next field typed into is a step of its own

  // ---- the page shown: the first, the following ones, or the PDF the sample makes on it as it stands
  function preview() {
    previewForm.elements.design.value = JSON.stringify(design);
    previewForm.requestSubmit();
  }
  root.querySelector('.paper-variants').addEventListener('click', e => {
    const v = e.target.closest('[data-variant]')?.dataset.variant;
    if (!v || v === variant) return;
    variant = v;
    if (v === 'pdf') preview();
    else if (picked && !shown(byId(picked))) picked = null;
    draw();
    inspect();
  });
  sample.addEventListener('change', preview);

  saveForm.addEventListener('submit', () => {
    saveForm.elements.design.value = JSON.stringify(design);
    saveForm.elements.name.value = name;
  });

  draw();
  inspect();
  document.fonts.ready.then(draw);
})();
