// A document or a Vorlage, edited as it prints (documents/paper.html): an A4 page in mm from the top left, as
// core/paper.py sets it; or the preview, the PDF it makes for a made-up person. A document (mayWord) is written in its
// text area (flow.js), its Vorlage around it as it stands, changed only where the Vorlage is. A Vorlage
// (mayDesign) is laid out under rulers, a document shown on it; a text on it is written where it is clicked. Drag an
// element to move it, a corner to size it; it
// snaps to half millimetres and to the page's middle and the edges of the others (Alt: freely), its place and size
// showing as it goes. Arrow keys nudge (Shift: 5 mm), Delete removes, Ctrl+D doubles, Ctrl+Z and Ctrl+Y undo and redo,
// Escape lets go. What is held fast („Festhalten“) lets clicks through to what lies beneath; the list of elements
// still picks it. The rail at the window's edge edits what is picked, else the document. What ConCampus fills in is a
// placeholder, shown by its name (words.js); only the preview fills it in.
(() => {
  'use strict';
  const root = document.getElementById('paper');
  if (!root) return;
  const data = JSON.parse(document.getElementById('paper-data').textContent);
  root.classList.toggle('may-design', data.mayDesign);  // a Vorlage: its elements picked, moved and written
  root.classList.toggle('may-word', data.mayWord);  // a document: its content written
  const desk = root.querySelector('.paper-desk'), board = root.querySelector('.paper-board'), sheet = root.querySelector('.paper-sheet');
  const panel = root.querySelector('.paper-panel'), frame = root.querySelector('.paper-frame');
  const rulers = {top: root.querySelector('.paper-ruler.top'), left: root.querySelector('.paper-ruler.left')};
  const saveForm = document.getElementById('paper-save'), previewForm = document.getElementById('paper-preview');
  const csrf = saveForm.querySelector('[name=csrfmiddlewaretoken]').value;
  const W = 210, H = 297, S = 8, PT = 25.4 / 72, SNAP = 0.5, CATCH = 1.5, LEADING = 1.25, RULER = 20, GUTTER = 28, REAL = 96 / 25.4;
  const COLOURS = {accent: ['Akzent', data.accent], ink: ['Schwarz', '#000000'], muted: ['Grau', '#5f5f5f'], line: ['Hellgrau', '#d6d6d6'], white: ['Weiß', '#ffffff']};
  const NAMES = {body: 'Textbereich', text: 'Text', wordmark: 'Schriftzug', picture: 'Bild', box: 'Fläche', address: 'Anschriftfeld', info: 'Infoblock'};
  const PAGES = {first: 'erste Seite', later: 'Folgeseiten', all: 'alle Seiten'};
  const WEIGHTS = {100: 'Dünn', 200: 'Extraleicht', 300: 'Leicht', 400: 'Normal', 500: 'Mittel', 600: 'Halbfett', 700: 'Fett', 800: 'Extrafett', 900: 'Schwarz'};  // core/paper.py WEIGHTS
  const faceName = f => `${WEIGHTS[f.w] || f.w}${f.i ? ' kursiv' : ''}`;
  const weightOf = el => el.weight || (el.bold ? 700 : 400);
  const ALIGN = [['left', 'Links'], ['center', 'Mitte'], ['right', 'Rechts']];
  const ADDRESS = [['Name', 'Erika Mustermann'], ['Straße und Hausnummer', 'Musterstraße 1'], ['PLZ und Ort', '12345 Musterstadt']];  // what an Anschriftfeld holds, and an example
  const GRIPS = ['nw', 'ne', 'sw', 'se'].map(c => `<i class="paper-grip" data-corner="${c}"></i>`).join('');

  let design = data.design, name = data.name, variant = 'first', picked = null, k = 1, writing = null, clicked = null;  // writing: the text on the page being written; clicked: a placeholder clicked in it
  // The content, written on the page (flow.js), the Hochschule's texts worded in it, for whom a copy stands and the
  // Vorlage it stands on: kept and undone with the page
  Object.assign(design, {flow: data.flow, texts: {}, programs: data.chosen, letterhead: data.letterhead});
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
  const typing = () => document.activeElement?.matches('input, textarea, select') || document.activeElement?.isContentEditable;
  const issuer = () => design.elements.some(el => el.type === 'info' || el.text?.includes('{dokument_amt}')) || JSON.stringify(design.flow.blocks).includes('"f":"amt"');  // printed somewhere, so asked for
  const flowEd = window.Flow({  // the content where the Hochschule writes it: in the text area, on the page
    data: data.compose, get: () => design.flow, texts: () => design.texts, editable: data.mayWord, panel,
    changed: field => changed(field || 'flow'),
    activated: () => pick(data.mayDesign && design.elements.find(el => el.type === 'body' && shown(el))?.id || null, true),  // its text area with it, to size and move
    undo: again => again ? restore(redos, undos) : restore(undos, redos),
  });

  // ---- changes: each one an undo step; typing into one field is one step
  function changed(field = null) {
    const now = JSON.stringify([design, name]);
    if (now === was) return;
    if (field === 'texts') asked();
    if (!field || field !== typed) {
      undos.push(was);
      if (undos.length > 100) undos.shift();
    }
    redos = [];
    typed = field;
    was = now;
    saveShown();
  }
  // The save button says whether anything differs from what is saved: quiet „Gespeichert“, or green „Speichern“; leaving
  // with something unsaved asks first (app.js, the form's dirty mark). Taken back to what was saved, nothing is.
  const saveButton = saveForm.querySelector('.paper-save'), saved = data.unsaved ? null : was;
  function saveShown() {
    const dirty = was !== saved;
    if (dirty) saveForm.dataset.dirty = '1';
    else delete saveForm.dataset.dirty;
    saveButton.disabled = !dirty;
    saveButton.textContent = dirty ? 'Speichern' : 'Gespeichert';
  }
  document.addEventListener('keydown', e => {  // Ctrl+S saves, in a field too, not the browser's „Seite speichern“
    if (!((e.ctrlKey || e.metaKey) && (e.key || '').toLowerCase() === 's')) return;
    e.preventDefault();
    if ('dirty' in saveForm.dataset) saveForm.requestSubmit(saveButton);
  });
  // Over the page: a text of the Hochschule's changed here, in how many documents it changes
  const asking = root.querySelector('.paper-ask');
  function asked() {
    asking.innerHTML = Object.values(data.compose.worded).filter(w => w.text in design.texts && w.uses > 1).map(w => `<div><p>„${esc(w.title)}“ ändert sich in allen ${w.uses} Dokumenten.</p></div>`).join('');
    asking.hidden = !asking.innerHTML;
  }
  function restore(from, to) {
    if (!from.length) return;
    to.push(was);
    was = from.pop();
    [design, name] = JSON.parse(was);
    typed = null;
    flowEd?.render();
    if (picked && !byId(picked)) picked = null;
    saveShown();
    asked();
    draw();
    inspect();
  }

  // ---- the placeholders a text on the page may hold (core/paper.py FIELDS), each with its name and its example
  const FIELDS = Object.fromEntries(data.fields.map(f => [f.key, f]));
  const label = key => FIELDS[key]?.label || key;
  const offered = () => Object.entries(data.fields.reduce((groups, f) => ((groups[f.group] ||= []).push([f.key, f.label, data.values[f.key]]), groups), {}));  // by whose they are
  const smallCaps = line => [...line].map(ch => ch !== ch.toUpperCase() ? `<span>${esc(ch.toUpperCase())}</span>` : esc(ch)).join('');

  // ---- the page's size: the whole page, the desk's width, or a share of real size ('100' is real size). The wheel
  // scrolls, as everywhere; with Ctrl (a trackpad's pinch sends it too) it zooms, the point under the pointer staying put.
  function measure() {
    const room = [desk.clientWidth - RULER - 2 * GUTTER, desk.clientHeight - RULER - 2 * GUTTER];
    return zoom === 'page' ? Math.min(room[0] / W, room[1] / H) : zoom === 'width' ? room[0] / W : clamp(+zoom || 100, 25, 400) / 100 * REAL;
  }
  function zoomTo(z, x, y) {  // keeping the page's point at screen point x, y where it is, else the middle of the desk
    const d = desk.getBoundingClientRect(), r = sheet.getBoundingClientRect();
    [x, y] = [x ?? d.left + d.width / 2, y ?? d.top + d.height / 2];
    const at = [(x - r.left) / k, (y - r.top) / k];
    zoom = String(z);
    localStorage.setItem('paper-zoom', zoom);
    draw();
    const now = sheet.getBoundingClientRect();
    desk.scrollLeft += now.left + at[0] * k - x;
    desk.scrollTop += now.top + at[1] * k - y;
  }
  // The control: − and + step through STEPS, the menu fits the page or picks a step, and names a size reached otherwise.
  const STEPS = [25, 50, 75, 100, 125, 150, 200, 300, 400], picker = root.querySelector('.paper-zoom select');
  function zoomShown() {
    const now = Math.round(k / REAL * 100), fit = zoom === 'page' || zoom === 'width';
    picker.innerHTML = `<option value="page">Ganze Seite</option><option value="width">Seitenbreite</option>` +
      [...new Set([...STEPS, now])].sort((a, b) => a - b).map(p => `<option value="${p}">${p} %</option>`).join('');
    picker.value = fit ? zoom : String(now);
    if (fit) picker.options[picker.selectedIndex].textContent = `${now} %`;  // a fit says how large it came out
  }
  picker.addEventListener('change', () => zoomTo(picker.value));
  root.querySelector('.paper-zoom').addEventListener('click', e => {
    const step = +e.target.closest('[data-step]')?.dataset.step;
    if (!step) return;
    const now = k / REAL * 100;
    zoomTo(step > 0 ? STEPS.find(p => p > now + 1) ?? 400 : [...STEPS].reverse().find(p => p < now - 1) ?? 25);
  });
  let wheeling = 0, wheel = 0, wheelAt = null;
  desk.addEventListener('wheel', e => {
    if (!(e.ctrlKey || e.metaKey) || variant === 'pdf') return;
    e.preventDefault();  // not the browser's own zoom
    wheel += e.deltaY;
    wheelAt = [e.clientX, e.clientY];
    wheeling ||= requestAnimationFrame(() => {
      wheeling = 0;
      zoomTo(clamp(k / REAL * 100 * Math.exp(-wheel * 0.002), 25, 400), ...wheelAt);
      wheel = 0;
    });
  }, {passive: false});
  new ResizeObserver(() => variant !== 'pdf' && Math.abs(measure() - k) > 0.001 && draw()).observe(desk);

  // ---- moving round the page: drag the grey, or anywhere with the Space bar held or the middle button
  let space = false;
  const quiet = target => target.closest?.('input, textarea, select, button, a, [contenteditable]');
  document.addEventListener('keydown', e => {
    if (e.code !== 'Space' || quiet(e.target) || variant === 'pdf') return;
    e.preventDefault();  // not the page scrolling
    space = true;
    desk.classList.add('paper-hand');
  });
  document.addEventListener('keyup', e => {
    if (e.code !== 'Space') return;
    space = false;
    desk.classList.remove('paper-hand');
  });
  desk.addEventListener('mousedown', e => { if (e.button === 1) e.preventDefault(); });  // not the browser's autoscroll
  desk.addEventListener('pointerdown', e => {
    const d = desk.getBoundingClientRect(), bar = e.clientX - d.left >= desk.clientWidth || e.clientY - d.top >= desk.clientHeight;  // its scrollbars stay its own
    const grey = e.button === 0 && !e.target.closest('.paper-sheet, .paper-ruler, .paper-corner');
    if (bar || !(grey || space || e.button === 1) || variant === 'pdf') return;
    e.preventDefault();
    e.stopPropagation();  // nothing on the page is picked or moved
    if (grey) {  // a click beside the page: done with what was written or picked there
      if (sheet.contains(document.activeElement)) document.activeElement.blur();
      if (picked) pick(null);
    }
    const from = [e.clientX, e.clientY, desk.scrollLeft, desk.scrollTop];
    desk.setPointerCapture(e.pointerId);
    desk.classList.add('paper-panning');
    const move = ev => {
      desk.scrollLeft = from[2] - (ev.clientX - from[0]);
      desk.scrollTop = from[3] - (ev.clientY - from[1]);
    };
    const up = () => {
      desk.removeEventListener('pointermove', move);
      desk.removeEventListener('pointerup', up);
      desk.removeEventListener('pointercancel', up);
      desk.classList.remove('paper-panning');
    };
    desk.addEventListener('pointermove', move);
    desk.addEventListener('pointerup', up);
    desk.addEventListener('pointercancel', up);
  }, true);

  // ---- the whole window for the workspace, the preview with it, over the page's head; the same button or Esc ends it
  // (the browser's own full screen, F11, takes away its tabs as well)
  const full = root.querySelector('.paper-full');
  const ENTER = 'M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4', LEAVE = 'M6 2v4H2M10 2v4h4M10 14v-4h4M6 14v-4H2';
  function fullShown(on) {
    root.classList.toggle('full', on);
    full.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="${on ? LEAVE : ENTER}"/></svg>${on ? '<span>Vollbild beenden</span>' : ''}`;
    full.title = on ? 'Vollbild beenden (Esc)' : 'Vollbild: nur die Seite und die Leiste';
    full.setAttribute('aria-label', on ? 'Vollbild beenden' : 'Vollbild');
    full.classList.toggle('on', on);
  }
  full.addEventListener('click', () => fullShown(!root.classList.contains('full')));
  fullShown(false);

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
    const pdf = variant === 'pdf', opened = pdf && frame.hidden;
    board.hidden = pdf;
    frame.hidden = !pdf;
    root.querySelector('.paper-zoom').hidden = pdf;
    root.querySelectorAll('[data-add]').forEach(b => { b.disabled = pdf || (b.dataset.add === 'address' || b.dataset.add === 'info') && design.elements.some(el => el.type === b.dataset.add); });
    if (pdf) return preview(opened ? 0 : 400);
    k = measure();
    zoomShown();
    Object.assign(sheet.style, {width: `${W * k}px`, height: `${H * k}px`});
    const first = variant === 'first';
    const guides = (first && data.letters ? `<div class="paper-window" style="${place({x: 20, y: 45, w: 85, h: 45})}"><small>Fenster DIN 5008</small></div>` : '') +
      (first && design.style.marks ? [105, 148.5, 210].map((y, n) => `<i class="paper-mark" style="left:${4 * S}px;top:${y * S}px;width:${(n === 1 ? 6 : 3.5) * S}px"></i>`).join('') : '');
    sheet.innerHTML = `<div class="paper-page" style="width:${W * S}px;height:${H * S}px;transform:scale(${k / S});--fit:${k / S}">` + guides +
      design.elements.filter(shown).map(element).join('') + '<i class="paper-guide v" hidden></i><i class="paper-guide h" hidden></i><span class="paper-size" hidden></span></div>';
    sheet.querySelector('[data-flow-host]')?.replaceWith(flowEd.node);  // the same, as it was: the cursor stays where it is
    marked();
  }
  const place = el => `left:${el.x * S}px;top:${el.y * S}px;width:${el.w * S}px;height:${el.h * S}px`;
  function element(el) {
    const st = design.style, font = el.font || st.font;
    let inner = '', css = place(el);
    if (el.type === 'text') {  // its words, written where they stand once clicked
      const on = el.id === writing ? ` contenteditable="true" spellcheck="false" data-writing="${esc(el.id)}"` : '';
      inner = `<div class="paper-lines"${on}>${Words.shown(Words.runs(el.text), label, key => data.values[key]) || (on ? '<br>' : '<span class="paper-raw">Text</span>')}</div>`;
      css += `;font-family:${family(font)};font-size:${px(el.size)}px;line-height:${el.leading || LEADING};color:${colour(el.colour)};text-align:${el.align || 'left'};` +
        `font-weight:${weightOf(el)};font-style:${el.italic ? 'italic' : 'normal'};text-decoration:${el.underline ? 'underline' : 'none'};text-transform:${el.caps ? 'uppercase' : 'none'}`;
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
      inner = ADDRESS.map(line => `<div>${Words.name(...line)}</div>`).join('');
      css += `;font-family:${family(st.font)};font-size:${px(el.size)}px;line-height:1.3`;
    } else if (el.type === 'info') {
      const holds = key => Words.name(label(key), data.values[key]);
      inner = `<div><b>${holds('dokument_amt')}</b></div><dl><dt>Aktenzeichen</dt><dd>${holds('dokument_aktenzeichen')}</dd><dt>Datum</dt><dd>${holds('dokument_datum')}</dd></dl>`;
      css += `;font-family:${family(st.font)};font-size:${px(el.size)}px`;
    } else if (el.type === 'body' && data.mayDesign) {  // on a Vorlage: only where each document's content goes, nothing of any one document
      inner = `<div class="paper-area"><b>Inhalt des Dokuments</b><small>${el.pages === 'later' ? 'Fortsetzung auf den Folgeseiten' : 'Wird in jedem Dokument geschrieben'}</small></div>`;
    } else if (el.type === 'body') {  // a document's content, written here on the first page; on the following ones, as it goes on from where the first one's area ends
      const first = design.elements.find(e => e.type === 'body' && e.pages !== 'later');
      const rest = variant === 'later' && first ? ` style="margin-top:-${first.h * S}px"` : '';
      const look = `text-align:${st.align};font-family:${family(st.font)};font-size:${px(st.size)}px;--em:${px(st.size)}px;--pt:${px(1)}px;--heading:${family(st.heading)};--title:${px(st.title)}px;--accent:${data.accent}`;
      inner = `<div class="paper-flow live${st.align === 'center' ? ' centred' : ''}${st.middle && variant === 'first' ? ' middle' : ''}${variant === 'later' ? ' rest' : ''}${st.bar ? ' barred' : ''}" style="${look}">` +
        `${variant === 'first' ? '<div data-flow-host></div>' : `<div class="flow"${rest}>${flowEd.html()}</div>`}</div>`;
    }
    const kind = `paper-el paper-${el.type}${el.type === 'box' && !el.fill ? ' frame' : ''}${el.id === picked ? ' on' : ''}${el.locked ? ' locked' : ''}`;
    return `<div class="${kind}" data-id="${esc(el.id)}" data-name="${named(el)}" style="${css}">${inner}${el.id === picked && !el.locked ? GRIPS : ''}</div>`;
  }

  function pick(id, content = false) {  // content: the text area, while its content is written
    if (id !== picked) panel.scrollTop = 0;  // what is picked anew shows from its top
    if (id && !content) flowEd.active = false;
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
    if (e.button !== 0 || e.target.closest('[data-writing]') || data.mayWord && e.target.closest('.flow')) return;  // in what is written the cursor goes where it is put
    if (!data.mayDesign) return;  // a document: only its text is in hand, the Vorlage around it is not
    const node = e.target.closest('.paper-el');
    sheet.focus({preventScroll: true});
    if (!node) return picked && pick(null);
    const el = byId(node.dataset.id), corner = e.target.dataset.corner, from = {...el}, x0 = e.clientX, y0 = e.clientY;
    if (el.id !== picked || flowEd.active) pick(el.id);
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
    const up = ev => {
      sheet.removeEventListener('pointermove', move);
      sheet.removeEventListener('pointerup', up);
      sheet.removeEventListener('pointercancel', up);
      if (!moved) return ev.type === 'pointerup' && el.type === 'text' && !corner && write(el, ev.clientX, ev.clientY);  // a click on words: written where it was clicked; a drag moves them
      changed();
      draw();
    };
    sheet.addEventListener('pointermove', move);
    sheet.addEventListener('pointerup', up);
    sheet.addEventListener('pointercancel', up);
  });
  function write(el, x, y) {
    writing = el.id;
    clicked = null;
    draw();
    clicked = x == null ? null : document.elementFromPoint(x, y)?.closest('[data-writing] .flow-pill')?.dataset.f || null;  // a placeholder clicked: the rail says what it is
    pick(el.id);
    const box = sheet.querySelector('[data-writing]'), point = document.caretRangeFromPoint?.(x, y), s = getSelection();
    box.focus();
    if (point && box.contains(point.startContainer)) {
      s.removeAllRanges();
      s.addRange(point);
    } else {
      s.selectAllChildren(box);
      s.collapseToEnd();
    }
  }
  Words.typing(sheet, {bold: host => host.matches('[data-writing]')});
  sheet.addEventListener('input', e => {
    const box = e.target.closest?.('[data-writing]'), el = box && byId(box.dataset.writing);
    if (!el) return;
    el.text = Words.notation(Words.read(box));
    changed(`${el.id}:text`);
  });
  sheet.addEventListener('click', e => {  // in a text being written: a placeholder clicked, said in the rail
    if (!e.target.closest?.('[data-writing]')) return;
    const f = e.target.closest('.flow-pill')?.dataset.f || null;
    if (f === clicked) return;
    clicked = f;
    inspect();
  });
  sheet.addEventListener('focusout', e => {  // left: written
    if (!e.target.matches?.('[data-writing]') || document.querySelector('.words-menu')) return;
    writing = null;
    draw();
    if (!panel.contains(e.relatedTarget)) inspect();  // into a field beside it: the field stays, to be typed in
  });
  function put(key, range) {  // a placeholder into the text picked: where its cursor stands, else at its end
    const el = byId(picked);
    if (el?.type !== 'text') return;
    if (writing !== el.id) write(el);
    const box = sheet.querySelector('[data-writing]'), s = getSelection();
    box.focus();
    if (range) {
      s.removeAllRanges();
      s.addRange(range);
    } else if (!s.rangeCount || !box.contains(s.anchorNode)) {
      s.selectAllChildren(box);
      s.collapseToEnd();
    }
    if (!key) return;
    Words.insert(s.getRangeAt(0), Words.pill({f: key, pre: ''}, label(key), data.values[key]));
    box.dispatchEvent(new Event('input', {bubbles: true}));
  }
  function values(button) {  // the menu of them, for where the cursor stands in the text picked, else its end
    put(null);
    const range = getSelection().rangeCount && getSelection().getRangeAt(0).cloneRange();
    if (range) Words.menu(range, offered(), key => put(key, range), button);
  }
  sheet.addEventListener('keydown', e => {  // { : the menu
    if (!e.target.closest?.('[data-writing]') || e.key !== '{') return;
    e.preventDefault();
    values();
  });
  Words.dropping(sheet, (range, key, e) => {  // one dragged from the menu onto a text: where it was let go
    const el = data.mayDesign && byId(e.target.closest?.('.paper-el')?.dataset.id);
    if (el?.type !== 'text') return;
    if (writing === el.id) {
      getSelection().removeAllRanges();
      getSelection().addRange(range);
    } else write(el, e.clientX, e.clientY);
    put(key);
  });

  // ---- keys: anywhere on the page but in a field
  document.addEventListener('keydown', e => {
    if (typing() || e.target.closest?.('.drawer, dialog')) return;
    const key = (e.key || '').toLowerCase(), mod = e.ctrlKey || e.metaKey;
    if (key === 'escape') return picked && variant !== 'pdf' ? pick(null) : root.classList.contains('full') && fullShown(false);  // first letting go, then leaving
    if (variant === 'pdf') return;
    if (mod && (key === 'z' || key === 'y')) {
      e.preventDefault();
      return key === 'z' && !e.shiftKey ? restore(undos, redos) : restore(redos, undos);
    }
    const el = picked && byId(picked);
    if (!el) return;
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
    text: () => ({type: 'text', w: 80, h: 8, text: '', size: 10, leading: LEADING, colour: 'ink', align: 'left', font: '', weight: 400, italic: false, underline: false, caps: false}),
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
    if (kind === 'text') write(el);
  }
  root.querySelector('.paper-add')?.addEventListener('click', e => {
    const kind = e.target.closest('[data-add]')?.dataset.add;
    if (!kind) return;
    if (kind !== 'picture') return add(kind);
    openDialog({kind: 'pictures', mode: 'add'});  // which picture, or a new one, before anything is placed
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

  // ---- uploads (documents.views.artworks): a picture, or the files of a font family, all at once
  async function upload(kind, files, extra = {}) {
    const form = new FormData();
    form.append('art', kind);
    files.forEach(file => form.append('datei', file));
    Object.entries(extra).forEach(([key, value]) => form.append(key, value));
    const r = await fetch(data.upload, {method: 'POST', body: form, headers: {'X-CSRFToken': csrf}, credentials: 'same-origin'}).catch(() => null);
    const d = r ? await r.json().catch(() => ({})) : {};
    if (!r?.ok) throw new Error(d.error || d.refused?.join(' ') || 'Hochladen fehlgeschlagen. Bitte die Verbindung prüfen.');
    return d;
  }
  // Files to upload, chosen or dropped; what came of them said in the dialog.
  const ACCEPT = {font: '.ttf,.otf,.woff,font/ttf,font/otf,font/woff', picture: 'image/png,image/jpeg'};
  function choose(kind) {
    return new Promise(resolve => {
      const input = Object.assign(document.createElement('input'), {type: 'file', multiple: kind === 'font', accept: ACCEPT[kind]});
      input.addEventListener('change', () => resolve(input.files.length ? send(kind, [...input.files]) : null));
      input.click();
    });
  }
  async function send(kind, files) {
    const font = kind === 'font';
    told({text: font ? `${files.length} Datei${files.length > 1 ? 'en werden' : ' wird'} gelesen …` : 'Lädt hoch …'});
    try {
      const d = await upload(kind, font ? files : files.slice(0, 1), font ? {lizenz: '1'} : {});
      if (!font) {
        data.pictures.push({pk: d.pk, name: d.name, url: d.url, w: d.w, h: d.h});
        told(null);
      } else {
        await Promise.all(d.kept.map(learn));
        const names = [...new Set(d.kept.map(f => f.name))];
        told(d.refused.length ? {text: `${d.kept.length} Schnitte aufgenommen. Nicht: ${d.refused.join(' ')}`, error: true}
          : {text: `Aufgenommen: ${names.join(', ')}, ${d.kept.map(faceName).join(', ')}.`});
      }
      return d;
    } catch (error) {
      told({text: error.message, error: true});
      return null;
    }
  }
  async function learn(d) {  // a face uploaded: known to the page's fonts and to the editor's
    const f = data.fonts[d.key] ||= {name: d.name, own: true, faces: []};
    f.faces = [...f.faces.filter(x => x.w !== d.w || x.i !== d.i), {pk: d.pk, w: d.w, i: d.i, url: d.url}].sort((a, b) => a.w - b.w || a.i - b.i);
    const face = new FontFace(`paper-${d.key}`, `url("${d.url}")`, {weight: String(d.w), style: d.i ? 'italic' : 'normal'});
    document.fonts.add(await face.load().catch(() => face));
    draw();
  }
  async function discard(pk, kind) {
    const r = await fetch(`${data.upload}${pk}/loeschen/`, {method: 'POST', headers: {'X-CSRFToken': csrf}, credentials: 'same-origin'}).catch(() => null);
    const d = r ? await r.json().catch(() => ({})) : {};
    if (!r?.ok) return told({text: d.error || 'Nicht gelöscht.', error: true});
    if (kind === 'picture') data.pictures = data.pictures.filter(p => p.pk !== pk);
    else for (const [key, f] of Object.entries(data.fonts)) {
      f.faces = f.faces.filter(x => x.pk !== pk);
      if (f.own && !f.faces.length) delete data.fonts[key];
    }
    told(null);
    draw();
    inspect();
  }

  // ---- the dialog for the Hochschule's own fonts, opened from where a font is chosen, and for pictures, opened
  // where one is placed: what there is, to use or delete, and the upload
  const dialog = root.querySelector('.paper-dialog');
  let view = null, said = null, licensed = false;  // view: {kind: 'fonts', into: the font field it was opened from} or {kind: 'pictures', mode: 'add' or 'swap'}
  function openDialog(v) {
    [view, said] = [v, null];
    dialogShown();
    if (!dialog.open) dialog.showModal();
  }
  function told(message) {  // a message in the dialog where it is open, else over the panel
    if (dialog.open) {
      said = message;
      dialogShown();
    } else {
      note = message;
      inspect();
    }
  }
  function dialogShown() {
    if (!view) return;
    const fonts = view.kind === 'fonts';
    dialog.innerHTML = `<header><h3 id="paper-dialog-title">${fonts ? 'Eigene Schriften' : view.mode === 'add' ? 'Bild einfügen' : 'Bild wählen'}</h3>` +
      '<button type="button" class="paper-x" data-close aria-label="Schließen">×</button></header>' +
      (said ? `<p class="flash${said.error ? ' error' : ''}">${esc(said.text)}</p>` : '') + (fonts ? fontsShown() : picturesShown());
  }
  // Where files are dropped or chosen: a frame of its own, what it takes said small beneath.
  const drop = (kind, words, takes, off) => `<div class="paper-drop${off ? ' off' : ''}" data-drop="${kind}"><span>${words}</span>` +
    `<button type="button" class="btn ghost small" data-upload="${kind}"${off ? ' disabled' : ''}>Auswählen</button><small>${takes}</small></div>`;
  function fontsShown() {
    const own = Object.entries(data.fonts).filter(([, f]) => f.own);
    return (own.length ? `<ul class="paper-families">${own.map(([key, f]) => `<li><b style="font-family:${family(key)}">${esc(f.name)}</b><span class="chips">` +
        f.faces.map(x => `<span class="chip">${faceName(x)}<button type="button" class="link" data-discard="${x.pk}" data-kind="font" title="${faceName(x)} löschen" aria-label="${faceName(x)} löschen">×</button></span>`).join('') + '</span></li>').join('')}</ul>`
        : '<p class="muted">Noch keine. Hochgeladen steht sie in jeder Schriftauswahl.</p>') +
      `<label class="option paper-licence"><input type="checkbox" data-licence autofocus${licensed ? ' checked' : ''}> Die Lizenz erlaubt, die Schrift in PDF-Dokumente einzubetten</label>` +
      drop('font', 'Schriftdateien hierher ziehen', 'TTF · OTF · WOFF · auch variabel · alle Schnitte einer Familie auf einmal', !licensed);
  }
  function picturesShown() {
    const grid = data.pictures.map((p, n) => `<li${p.pk === byId(picked)?.artwork ? ' class="on"' : ''}><button type="button" data-picture="${p.pk}"${n ? '' : ' autofocus'} title="${esc(p.name)} ${view.mode === 'add' ? 'einfügen' : 'wählen'}"><img src="${esc(p.url)}" alt=""><small>${esc(p.name)}</small></button>` +
      `<button type="button" class="paper-delete" data-discard="${p.pk}" data-kind="picture" title="Löschen, wo kein Dokument es verwendet" aria-label="${esc(p.name)} löschen">×</button></li>`).join('');
    return (grid ? `<ul class="paper-pictures">${grid}</ul>` : '') + drop('picture', grid ? 'Neues Bild hierher ziehen' : 'Siegel, Unterschrift, Ornament hierher ziehen', 'PNG oder JPEG · bis 5 MB');
  }
  function placed(pk) {  // a picture chosen: onto the page, or into the picture picked
    const pic = data.pictures.find(p => p.pk === pk), mode = view.mode;
    dialog.close();
    if (mode === 'add') add('picture', sized(pic));
    else set('artwork', pk);
  }
  dialog.addEventListener('click', e => {
    const b = e.target.closest('button');
    if (e.target === dialog || b?.dataset.close !== undefined) return dialog.close();  // a click beside it, or ×
    if (!b) return;
    if (b.dataset.picture) return placed(+b.dataset.picture);
    if (b.dataset.discard) return discard(+b.dataset.discard, b.dataset.kind);
    if (b.dataset.upload) choose(b.dataset.upload).then(uploaded);
  });
  function uploaded(d) {  // a picture uploaded goes where it was wanted; a font, into the menu it was opened from
    if (d?.pk) placed(d.pk);
    else if (d?.kept?.length && view?.into) set(view.into, d.kept[0].key);
  }
  dialog.addEventListener('change', e => {
    if (!e.target.matches('[data-licence]')) return;
    licensed = e.target.checked;
    dialogShown();
  });
  dialog.addEventListener('dragover', e => {
    const zone = e.target.closest?.('[data-drop]');
    if (!zone || zone.classList.contains('off')) return;
    e.preventDefault();  // it may be dropped here
    zone.classList.add('over');
  });
  dialog.addEventListener('dragleave', e => e.target.closest?.('[data-drop]')?.classList.remove('over'));
  dialog.addEventListener('drop', e => {
    const zone = e.target.closest?.('[data-drop]');
    if (!zone) return;
    e.preventDefault();
    zone.classList.remove('over');
    if (zone.classList.contains('off')) return;
    const files = [...e.dataTransfer.files].filter(f => zone.dataset.drop === 'font' ? /\.(ttf|otf|woff)$/i.test(f.name) : /^image\/(png|jpeg)$/.test(f.type));
    if (!files.length) return told({text: zone.dataset.drop === 'font' ? 'Keine Schriftdatei darunter: TTF, OTF oder WOFF.' : 'Kein Bild darunter: PNG oder JPEG.', error: true});
    send(zone.dataset.drop, files).then(uploaded);
  });
  dialog.addEventListener('close', () => { if (!dialog.open) view = null; });  // the event comes later: not when it opened again meanwhile

  // ---- the panel: what is picked, else the document
  const field = (label, html, narrow = false) => `<div${narrow ? ' class="paper-narrow"' : ''}><label>${label}</label>${html}</div>`;
  const number = (key, value, step, min, max) => `<input type="number" data-key="${key}" value="${value ?? ''}" step="${step}" min="${min}" max="${max}">`;
  const check = (key, label, on) => `<label class="option"><input type="checkbox" data-key="${key}"${on ? ' checked' : ''}> ${label}</label>`;
  const options = (pairs, value) => pairs.map(([v, l]) => `<option value="${esc(v)}"${v === value ? ' selected' : ''}>${esc(l)}</option>`).join('');
  // A font menu: ConCampus's fonts, the Hochschule's own, and at its end the way to add one (openDialog).
  const fontPick = (key, value, inherit) => `<select data-key="${key}">${inherit ? '<option value="">Wie der Text</option>' : ''}` +
    `<optgroup label="Mitgeliefert">${options(Object.entries(data.fonts).filter(([, f]) => !f.own).map(([v, f]) => [v, f.name]), value)}</optgroup>` +
    (Object.values(data.fonts).some(f => f.own) ? `<optgroup label="Eigene">${options(Object.entries(data.fonts).filter(([, f]) => f.own).map(([v, f]) => [v, f.name]), value)}</optgroup>` : '') +
    '<option value="+">Eigene Schrift hochladen …</option></select>';
  const fontsLink = '<button type="button" class="link paper-aside" data-open="fonts">Eigene Schriften</button>';  // in a section's heading, at its end
  const settings = data.settings ? `<p><a href="${esc(data.settings)}">In den Einstellungen der Hochschule ändern <i class="arrow"></i></a></p>` : '<p class="muted paper-hint"><small>Aus den Einstellungen der Hochschule.</small></p>';  // where its own values change
  // The weights the text's family has, as it names them; the one set kept where the family lacks it (paper.py nearest).
  function weightPick(el) {
    const now = weightOf(el), faces = data.fonts[el.font || design.style.font]?.faces || [];
    const weights = [...new Set([...faces.map(f => f.w), now])].sort((a, b) => a - b);
    return `<select data-key="weight">${weights.map(w => `<option value="${w}"${w === now ? ' selected' : ''}>${WEIGHTS[w] || w}${faces.some(f => f.w === w) ? '' : ' (fehlt, die nächste steht)'}</option>`).join('')}</select>`;
  }
  const segments = (key, pairs, value) => `<div class="paper-seg">${pairs.map(([v, l]) => `<button type="button" data-key="${key}" data-value="${v}"${v === value ? ' class="on"' : ''}>${l}</button>`).join('')}</div>`;
  const swatches = (key, value) => `<div class="paper-swatches">${Object.entries(COLOURS).map(([v, [l, c]]) =>
    `<button type="button" data-key="${key}" data-value="${v}" title="${l}" style="background:${c}"${v === value ? ' class="on"' : ''}></button>`).join('')}` +
    `<input type="color" data-key="${key}" value="${/^#/.test(value) ? value : colour(value)}" title="Eigene Farbe"></div>`;
  const section = (title, html, aside = '') => `<section class="paper-section"><h4>${title}${aside}</h4>${html}</section>`;

  function inspect() {
    const el = picked && byId(picked), open = new Set([...panel.querySelectorAll('details[open] > summary')].map(s => s.firstChild.textContent));
    const content = data.mayWord && flowEd.active && flowEd.panel();  // the rail for the content while it is written, the way back to the document over it
    panel.innerHTML = (note ? `<p class="flash${note.error ? ' error' : ''}">${esc(note.text)}</p>` : '') +
      (content ? `<p class="paper-crumb"><button type="button" class="link" data-act="none"><i class="arrow left"></i> Dokument</button></p>${content}` : el ? elementPanel(el) : documentPanel());
    panel.querySelectorAll('details > summary').forEach(s => { if (open.has(s.firstChild.textContent)) s.parentElement.open = true; });  // what was open stays so
  }
  const officeField = () => field('Ausgestellt von', `<input data-key="office" value="${esc(design.flow.office)}" placeholder="${esc(data.compose.values.hochschule)}" maxlength="120">`);
  function elementPanel(el) {
    let html = `<p class="paper-crumb"><button type="button" class="link" data-act="none"><i class="arrow left"></i> Vorlage</button></p><h3>${named(el)}</h3>`;  // only a Vorlage's elements are picked
    if (el.type === 'text') {
      const f = writing === el.id && FIELDS[clicked], issued = clicked === 'dokument_amt' && !data.letters && data.mayWord;  // a placeholder clicked in it: what it is, and where it is changed
      html += '<div class="paper-type"><button type="button" data-tool="value" title="Platzhalter einsetzen, wo die Schreibmarke steht; auch mit {"><b>{ }</b> Platzhalter</button></div>' + (f ? `<p class="paper-hint"><b>${esc(f.label)}</b>${data.values[f.key] ? ` · ${f.example ? 'z. B. ' : ''}${esc(data.values[f.key])}` : ''}</p>` + (issued ? officeField() : f.example ? '' : settings) : '') +
        section('Schrift', field('Schrift', fontPick('font', el.font || '', true)) +
          `<div class="row">${field('Größe (pt)', number('size', el.size, 0.5, 4, 72), true)}${field('Zeilenabstand', number('leading', el.leading ?? LEADING, 0.05, 0.8, 3), true)}</div>` +
          field('Stärke', weightPick(el)) +
          `<div class="paper-checks">${check('italic', 'Kursiv', el.italic)}${check('underline', 'Unterstrichen', el.underline)}${check('caps', 'Großbuchstaben', el.caps)}</div>` +
          field('Farbe', swatches('colour', el.colour)) + field('Ausrichtung', segments('align', ALIGN, el.align || 'left')), fontsLink);
    } else if (el.type === 'wordmark') {
      html += (data.logo ? `<p class="muted"><small>Das Logo der Hochschule, so groß der Rahmen es fasst.${data.svg ? ' Als SVG steht im PDF der Schriftzug; für das PDF dort ein PNG hochladen.' : ''}</small></p>` :
        '<p class="muted"><small>Der Schriftzug der Hochschule, in der Schrift des Texts.</small></p>') + settings +
        `<div class="row">${field('Größe (pt)', number('size', el.size, 0.5, 6, 60), true)}</div>` + field('Ausrichtung', segments('align', ALIGN, el.align));
    } else if (el.type === 'picture') {
      const pic = data.pictures.find(p => p.pk === el.artwork);
      html += `<div class="paper-picked">${pic ? `<img src="${esc(pic.url)}" alt=""><span>${esc(pic.name)}</span>` : '<span class="muted">Bild fehlt</span>'}` +
        '<button type="button" class="btn ghost small" data-open="pictures">Anderes Bild</button></div>' +
        '<p class="muted"><small>So groß der Rahmen es fasst, in seinen Maßen.</small></p>';
    } else if (el.type === 'box') {
      html += field('Farbe', swatches('colour', el.colour)) + field('Art', segments('fill', [['1', 'Fläche'], ['0', 'Rahmen']], el.fill ? '1' : '0')) +
        (el.fill ? '' : `<div class="row">${field('Linie (pt)', number('line', el.line, 0.1, 0.1, 10), true)}</div>`);
    } else if (el.type === 'address' || el.type === 'info') {
      html += `<p class="muted"><small>${el.type === 'address' ? 'Name und Anschrift der Empfänger:in, aus ihrem Konto. Im Fenster des Umschlags: links 20, oben 45 mm, 85 × 45 mm nach DIN 5008.' : 'Das Amt, das den Brief schickt, sein Aktenzeichen und sein Datum.'}</small></p>` +
        `<div class="row">${field('Größe (pt)', number('size', el.size, 0.5, 6, 14), true)}</div>`;
    } else if (el.type === 'body') {
      html += `<p class="muted"><small>Hier steht der Inhalt jedes Dokuments auf dieser Vorlage, ${el.pages === 'first' ? 'auf der ersten Seite' : 'auf jeder weiteren'}. Wie er aussieht: unter „Vorlage“, „Schrift des Inhalts“; wie er sich liest: in der Vorschau.</small></p>`;
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
  // What an element shows, in a few words: its text with its placeholders by name, else what it is.
  function summary(el) {
    if (el.type === 'text') return el.text.replace(/\{(\w+)\}/g, (m, n) => label(n)).replace(/\*\*/g, '').trim() || 'leer';
    if (el.type === 'picture') return data.pictures.find(p => p.pk === el.artwork)?.name || 'Bild fehlt';
    if (el.type === 'box') return `${mm(el.w)} × ${mm(el.h)} mm`;
    return {wordmark: data.logo ? 'Logo der Hochschule' : data.wordmark.join(' '), address: 'Name und Anschrift der Empfänger:in',
            info: 'Amt, Aktenzeichen und Datum', body: 'Inhalt des Dokuments'}[el.type];
  }
  const LOCK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4"/></svg>';  // core/access.py LOCK
  // Nothing picked: a document itself, its name, the Vorlage it stands on, for whom a copy stands, who issues it; or
  // a Vorlage itself, its name, the documents on it, its type and what its pages hold.
  function documentPanel() {
    const st = design.style;
    const list = design.elements.filter(shown).reverse().map(el => `<li data-hint="${esc(el.id)}"><button type="button" data-pick="${esc(el.id)}">` +
      `<small>${named(el)}</small><span>${esc(summary(el))}</span></button>` +
      `<button type="button" class="paper-lock${el.locked ? ' on' : ''}" data-lock="${esc(el.id)}" title="${el.locked ? 'Festgehalten: lösen' : 'Festhalten: nicht verschieben, Klicks gehen hindurch'}" aria-pressed="${!!el.locked}">${LOCK}</button></li>`).join('');
    const head = data.mayWord && data.letterheads[design.letterhead];
    return (data.mayWord ? '<p class="paper-start">Zum Ändern in den Text klicken und schreiben.</p>' : '') +
      field('Name', `<input data-key="name" value="${esc(name)}" maxlength="120">`) +
      (data.when ? `<p class="paper-hint"><small class="muted">Wann ConCampus es ${data.letters ? 'schickt' : 'ausstellt'}</small><br>${esc(data.when)}` +
        (data.where ? `<br><a href="${esc(data.where.url)}"><small>${esc(data.where.name)} <i class="arrow"></i></small></a>` : '') + '</p>' : '') +
      (head ? field('Vorlage',`<select data-key="letterhead">${options(Object.entries(data.letterheads).map(([k, h]) => [k, h.name]), design.letterhead)}</select>` +
        `<small class="muted">Kopf, Fuß und Schrift${data.designs ? ` · <a href="${esc(head.url)}">„${esc(head.name)}“ gestalten <i class="arrow"></i></a>` : ', gestaltet für alle Dokumente darauf'}</small>`) : '') +
      (data.standing ? `<p class="muted paper-hint"><small>Gilt für ${data.standing.length === 1 ? 'ein Dokument' : `alle ${data.standing.length} Dokumente`} darauf. Die Vorschau zeigt „${esc(data.sample)}“ auf ihr.</small></p>` : '') +
      (data.copy ? `<p class="muted paper-hint"><small>Variante von „${esc(data.occasion)}“: gilt statt seiner für die Studiengänge, die Sie hier wählen.</small></p>` : '') +
      (data.free ? section('Verschicken', data.compose_url  // who may write to students, straight there, this chosen; anyone else, who does
        ? `<div class="actions"><a class="btn small" href="${esc(data.compose_url)}">An Studierende schicken</a></div><p class="muted paper-hint"><small>Dort ankreuzen, an wen, dann „Nachricht schreiben“. Jede Person bekommt es ausgefüllt ins Postfach.</small></p>`
        : '<p class="muted paper-hint"><small>Wer Studierenden schreiben darf, schickt es: unter Studierende Personen ankreuzen, „Nachricht schreiben“, dieses Dokument wählen.</small></p>') : '') +
      (data.copy && data.mayWord ? section('Gilt für', `<div class="paper-programs">${data.programs.map(([pk, title]) =>
        `<label class="option"><input type="checkbox" data-program="${pk}"${design.programs.includes(pk) ? ' checked' : ''}> ${esc(title)}</label>`).join('')}</div>` +
        (design.programs.length ? '' : '<p class="muted paper-hint"><small>Noch für niemanden: bis ein Studiengang gewählt ist, bekommt jeder das Dokument des Anlasses.</small></p>')) : '') +
      (!data.letters && !data.free && data.mayWord && issuer() ? officeField() : '') +  // one of the Hochschule's own: who sends it signs
      (data.mayDesign ? section('Schrift des Inhalts', `<div class="row">${field('Schrift', fontPick('style.font', st.font))}${field('Überschriften', fontPick('style.heading', st.heading))}</div>` +
        `<div class="row">${field('Größe (pt)', number('style.size', st.size, 0.5, 7, 16), true)}${field('Titel (pt)', number('style.title', st.title, 1, 10, 48), true)}</div>` +
        `<div class="paper-checks">${check('style.bar', 'Balken über dem Titel', st.bar)}${check('style.center', 'Zentriert', st.align === 'center')}` +
        `${check('style.middle', 'Senkrecht in der Mitte', st.middle)}${check('style.marks', 'Falz- und Lochmarken', st.marks)}</div>`, fontsLink) : '') +
      (variant === 'pdf' || !data.mayDesign ? '' : section(variant === 'first' ? 'Auf der ersten Seite' : 'Auf den Folgeseiten', `<ul class="paper-list">${list}</ul>`)) +
      (data.standing?.length ? `<section class="paper-section"><details><summary>Die Dokumente darauf</summary><ul class="paper-standing">` +
        `${data.standing.map(([n, url]) => `<li><a href="${esc(url)}">${esc(n)}</a></li>`).join('')}</ul></details></section>` : '');
  }
  // The visible fields follow what was dragged or nudged.
  function fields() {
    const el = picked && byId(picked);
    if (el) ['x', 'y', 'w', 'h'].forEach(a => { const input = panel.querySelector(`input[data-key="${a}"]`); if (input && document.activeElement !== input) input.value = el[a]; });
  }
  function set(key, value, input) {
    const el = picked && byId(picked);
    if (key === 'name') {
      name = value.slice(0, 120);
      return changed('name');
    }
    if (key === 'office') {  // who issues it, part of its content
      design.flow.office = value.slice(0, 120);
      return changed('flow');
    }
    if (key === 'letterhead') {  // another Vorlage: the document on it as it prints there
      Object.assign(design, structuredClone(data.letterheads[value].page), {letterhead: value});
      changed();
      draw();
      return inspect();
    }
    if (key === 'style.center') design.style.align = value ? 'center' : 'left';
    else if (key.startsWith('style.')) design.style[key.slice(6)] = value;
    else if (!el) return;
    else if (key === 'fill') el.fill = value === '1';
    else if (key === 'weight') [el.weight, el.bold] = [+value, undefined];
    else el[key] = value;
    changed(input ? `${picked}:${key}` : null);
    draw();
    if (!input) inspect();
  }
  panel.addEventListener('input', e => {
    const t = e.target, key = t.dataset.key;
    if (t.dataset.program) {  // for whom a copy stands
      const pk = +t.dataset.program;
      design.programs = t.checked ? [...design.programs, pk] : design.programs.filter(x => x !== pk);
      changed('programs');
      return inspect();
    }
    if (!key) return;
    if (t.value === '+') {  // a font menu's last entry: add one, then set it here
      inspect();  // the menu as it was
      return openDialog({kind: 'fonts', into: key});
    }
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
    if (b.dataset.tool === 'value') return flowEd.active || values(b);  // into the content, flow.js puts it
    if (b.dataset.pick) return pick(b.dataset.pick);
    if (b.dataset.lock) {
      const held = byId(b.dataset.lock);
      held.locked = !held.locked;
      changed();
      draw();
      return inspect();
    }
    if (b.dataset.open === 'fonts') return openDialog({kind: 'fonts'});
    if (b.dataset.open === 'pictures') return openDialog({kind: 'pictures', mode: 'swap'});
    if (b.dataset.act === 'none') {  // back to the document or Vorlage itself
      flowEd.active = false;
      return pick(null);
    }
    if (!el) return;
    if (b.dataset.act === 'front' || b.dataset.act === 'back') return layer(el, b.dataset.act === 'front');
    if (b.dataset.act === 'copy') return duplicate(el);
    if (b.dataset.act === 'remove') return remove(el);
  });
  panel.addEventListener('focusout', () => { typed = null; });  // the next field typed into is a step of its own
  const hint = (e, on) => { const id = e.target.closest?.('[data-hint]')?.dataset.hint; if (id) nodeOf(id)?.classList.toggle('hint', on); };  // a row of the list shows its element
  panel.addEventListener('mouseover', e => hint(e, true));
  panel.addEventListener('mouseout', e => hint(e, false));

  // ---- the page shown: the first, the following ones, or the PDF the sample makes on it as it stands. What is sent:
  // a document's content, the Hochschule's texts worded in it, for whom it stands and its Vorlage; a Vorlage's page.
  const posted = () => {
    const {flow, texts, programs, letterhead, ...page} = design;
    return JSON.stringify(data.mayWord ? {content: flow, texts, programs, letterhead, name} : {page, name});
  };
  let previewing = 0, seen = 0;  // seen: how far down the page was looked at when the preview was opened
  function preview(wait) {  // the page alone, where and as large as it was written, and as far down; a change beside it shown once it has settled
    const size = Math.round(k / REAL * 100);
    Object.assign(frame.style, {width: `${W * k + 32}px`, height: `${Math.min(desk.clientHeight - RULER - GUTTER, H * k + 12)}px`});  // room for the browser's scrollbar beside the page
    clearTimeout(previewing);
    previewing = setTimeout(() => {
      previewForm.elements.design.value = posted();
      previewForm.action = `${previewForm.action.split('#')[0]}#toolbar=0&navpanes=0&zoom=${size},0,${Math.round(seen / size * 100)}`;
      previewForm.requestSubmit();
    }, wait);
  }
  root.querySelector('.paper-variants').addEventListener('click', e => {
    const v = e.target.closest('[data-variant]')?.dataset.variant, back = variant === 'pdf';
    if (!v || v === variant) return;
    variant = v;
    if (v === 'pdf') {  // nothing picked: beside the preview, what the document is
      seen = desk.scrollTop;
      flowEd.active = picked = null;
    } else if (picked && !shown(byId(picked))) picked = null;
    draw();
    if (back) desk.scrollTop = seen;  // and the page again as it was left
    inspect();
  });
  saveForm.addEventListener('submit', () => { saveForm.elements.design.value = posted(); });

  saveShown();
  draw();
  inspect();
  document.fonts.ready.then(draw);
})();
