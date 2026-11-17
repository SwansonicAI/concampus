// A document's content, written where it prints (documents.compose; paper.js holds it in the text area). It reads as
// the document does: paragraphs, each in a style, and tables of a list. What ConCampus fills in shows as a tinted pill,
// its value for a made-up person. Enter, Backspace, Tab and Ctrl+B do what they do in a word processor; { puts in a
// value; the toolbar sets a paragraph's style and adds a table. The rail says only what is picked: a value, whose it is;
// a paragraph, when it shows; a table, its list and columns. Words are kept as runs (words.js).
window.Flow = function Flow({data, get, texts, changed, editable, panel, toolbar, activated, undo}) {
  const W = window.Words, esc = W.esc;
  const PARTS = new Set(['fact', 'sign', 'signatures']), HEADS = new Set(['title', 'heading']);  // a Tab divides the lines of PARTS
  const fields = Object.fromEntries(data.fields.map(([key, label, group]) => [key, {label, group}]));
  const node = Object.assign(document.createElement('div'), {className: 'flow', lang: 'de'});
  let at = null, picked = null, saved = null, active = false, steps = 0;  // the block the cursor is in; the value picked; where the cursor was
  const step = () => changed(`flow#${++steps}`);  // a change of its own to undo; typing in between is one

  const blocks = () => get().blocks;
  const listOf = n => blocks()[n]?.type === 'table' ? blocks()[n].list : null;
  const labelOf = (key, list) => data.lists[list]?.fields.find(f => f[0] === key)?.[1] || fields[key]?.label || key;
  const valueOf = (key, row) => String((row && key in row ? row[key] : data.values[key]) ?? '');
  const said = (runs, row) => (runs || []).map(r => 'f' in r ? valueOf(r.f, row) && (r.pre || '') + valueOf(r.f, row) : r.t).join('').replace(/\t/g, '').trim();
  const option = (value, label, now) => `<option value="${esc(value)}"${value === now ? ' selected' : ''}>${esc(label)}</option>`;
  const whenPick = b => `<div><label>Zeigen</label><select data-flow="when">${option('', 'immer', b.when || '')}${data.fields.map(([key, label]) => option(key, `nur, wo ${label} gegeben ist`, b.when || '')).join('')}</select></div>`;

  // ---- runs as units, a character or a value each, to split, join and count as a word processor does
  const units = runs => runs.flatMap(r => 'f' in r ? [r] : [...r.t].map(t => r.b ? {t, b: true} : {t}));
  const compact = us => us.reduce((out, u) => {
    const last = out[out.length - 1];
    if ('t' in u && last && 't' in last && !!last.b === !!u.b) last.t += u.t;
    else out.push({...u});
    return out;
  }, []);
  const cells = runs => {
    const out = [[]];
    units(runs).forEach(u => u.t === '\t' ? out.push([]) : out[out.length - 1].push(u));
    return out.map(compact);
  };

  // ---- on the page
  const shown = (runs, row, list) => W.shown(runs, key => valueOf(key, row), key => labelOf(key, list));
  const condition = b => b.when ? ` data-when="${esc(b.when)}" data-if="${esc(`nur, wo ${labelOf(b.when)} gegeben ist`)}"` : '';
  function paragraph(b, n) {
    const inner = PARTS.has(b.style)
      ? cells(b.runs).map(c => `<span class="flow-cell${b.style === 'signatures' && c.length && !said(c) ? ' off' : ''}">${shown(c) || '<br>'}</span>`).join('')
      : shown(b.runs) || '<br>';
    return `<p class="flow-p s-${b.style}${b.when && !valueOf(b.when) ? ' off' : ''}" data-n="${n}" data-style="${b.style}"${condition(b)}>${inner}</p>`;
  }
  function table(b, n) {
    const rows = data.lists[b.list]?.rows.length ? data.lists[b.list].rows : [{}];
    const cls = c => [c.right && 'num', c.head.length && !said(c.head) && 'off'].filter(Boolean).join(' ');  // a column whose heading says nothing falls away
    const cell = (tag, c, i, part, row, r) => `<${tag} class="${cls(c)}"${r ? '' : ` data-cell="${n}.${i}.${part}"${editable ? ' contenteditable="true"' : ''}`}>` +
      `${shown(c[part], row, b.list) || '<br>'}</${tag}>`;
    return `<div class="flow-table${b.when && !valueOf(b.when) ? ' off' : ''}" contenteditable="false" data-n="${n}"${condition(b)}><table>` +
      `<thead><tr>${b.columns.map((c, i) => cell('th', c, i, 'head', null, 0)).join('')}</tr></thead><tbody>` +
      rows.map((row, r) => `<tr${r ? ' class="flow-sample"' : ''}>${b.columns.map((c, i) => cell('td', c, i, 'cell', row, r)).join('')}</tr>`).join('') + '</tbody></table></div>';
  }
  const markup = () => blocks().map((b, n) => b.type === 'table' ? table(b, n) : paragraph(b, n)).join('');
  function render() {
    if (!blocks().length) blocks().push({type: 'p', style: 'text', runs: []});
    node.innerHTML = markup();
    node.contentEditable = editable ? 'true' : 'false';
    picked = null;
  }
  function html() {  // read-only, for the following pages
    const was = editable;
    editable = false;
    const out = markup();
    editable = was;
    return out;
  }

  // ---- back from the page, as the browser left it
  function readParagraph(el, exact = false) {
    if (!PARTS.has(el.dataset.style)) return W.read(el, exact);
    const out = [];
    let first = true;
    for (const c of el.childNodes) {
      if (c.classList?.contains('flow-cell')) {
        if (!first) out.push({t: '\t'});
        out.push(...W.read(c, exact));
        first = false;
      } else {  // typed beside the cells: into the one before
        const holder = document.createElement('span');
        holder.append(c.cloneNode(true));
        out.push(...W.read(holder, exact));
      }
    }
    return compact(units(out));
  }
  function sync() {
    let holder = null;
    for (const c of [...node.childNodes]) {  // what the browser put beside the paragraphs, into one
      if (c.nodeType === Node.ELEMENT_NODE && c.matches('p, .flow-table')) { holder = null; continue; }
      if (!holder) {
        holder = Object.assign(document.createElement('p'), {className: 'flow-p s-text'});
        holder.dataset.style = 'text';
        c.before(holder);
      }
      holder.append(c);
    }
    const was = blocks(), out = [];
    for (const el of node.children) {
      if (el.classList.contains('flow-table')) {
        if (was[+el.dataset.n]) out.push(was[+el.dataset.n]);
        continue;
      }
      out.push({type: 'p', style: el.dataset.style || 'text', runs: readParagraph(el), ...(el.dataset.when ? {when: el.dataset.when} : {})});
    }
    get().blocks = out;
    [...node.children].forEach((el, n) => { el.dataset.n = n; });
  }

  // ---- the cursor, as how far into its paragraph it stands, in units
  const blockEl = n => node.querySelector(`:scope > [data-n="${n}"]`);
  function where(range = getSelection().rangeCount && getSelection().getRangeAt(0)) {
    const el = range && (range.startContainer.nodeType === Node.ELEMENT_NODE ? range.startContainer : range.startContainer.parentElement)?.closest('[data-n]');
    if (!el || el.parentElement !== node || el.matches('.flow-table')) return null;
    const before = document.createRange();
    before.setStart(el, 0);
    before.setEnd(range.startContainer, range.startOffset);
    const holder = document.createElement('p');
    holder.dataset.style = el.dataset.style;
    holder.append(before.cloneContents());
    return {n: +el.dataset.n, k: units(readParagraph(holder, true)).length, collapsed: range.collapsed};
  }
  function place(n, k) {
    const el = blockEl(n);
    if (!el) return;
    const r = document.createRange();
    let left = k, done = false, prev = null;
    const stop = (fn) => { fn(); done = true; };
    (function visit(parent) {
      for (const c of parent.childNodes) {
        if (done) return;
        if (c.classList?.contains('flow-cell')) {
          if (prev) {  // the Tab before a part
            if (left === 0) return stop(() => { r.selectNodeContents(prev); r.collapse(false); });
            left -= 1;
          }
          prev = c;
          visit(c);
        } else if (c.nodeType === Node.TEXT_NODE) {
          if (left <= c.length) return stop(() => r.setStart(c, left));
          left -= c.length;
        } else if (c.nodeName === 'BR' || c.dataset?.f) {
          if (left === 0) return stop(() => r.setStartBefore(c));
          left -= 1;
        } else visit(c);
      }
    })(el);
    if (!done) {
      r.selectNodeContents(prev || el);
      r.collapse(false);
    }
    r.collapse(true);
    getSelection().removeAllRanges();
    getSelection().addRange(r);
  }
  function redo(n, k) {  // the blocks drawn anew, the cursor where it belongs, the toolbar and rail for where that is
    render();
    place(n, k);
    at = n;
    active = true;
    step();
    activated();
  }

  // ---- typing
  node.addEventListener('input', e => {
    const cell = e.target.closest?.('[data-cell]');
    if (cell) {
      const [n, i, part] = cell.dataset.cell.split('.');
      blocks()[+n].columns[+i][part] = W.read(cell);
    } else sync();
    changed();
  });
  node.addEventListener('keydown', e => {
    if (e.isComposing) return;
    const key = e.key, mod = e.ctrlKey || e.metaKey, cell = e.target.closest?.('[data-cell]');
    if (mod && (key.toLowerCase() === 'z' || key.toLowerCase() === 'y')) {
      e.preventDefault();
      const w = where(), size = n => units(blocks()[n]?.runs || []).length, after = w && size(w.n) - w.k;
      undo(key.toLowerCase() === 'y' || e.shiftKey);
      if (!w || !blocks().length) return;  // the blocks drawn anew: the cursor as far from its paragraph's end as it stood, behind what was typed
      at = Math.min(w.n, blocks().length - 1);
      node.focus({preventScroll: true});
      place(at, Math.max(0, size(at) - after));
      return activated();
    }
    if (mod && key.toLowerCase() === 'b') {
      e.preventDefault();
      return document.execCommand('bold');
    }
    if (key === '{') {
      e.preventDefault();
      return values();
    }
    if (cell) {
      if (key === 'Enter' || key === 'Tab') {
        e.preventDefault();
        if (key === 'Enter' && cell.tagName === 'TD') document.execCommand('insertLineBreak');
      }
      return;
    }
    if (key === 'Enter' && e.shiftKey) {
      e.preventDefault();
      return document.execCommand('insertLineBreak');
    }
    if (!['Enter', 'Backspace', 'Delete', 'Tab'].includes(key)) return;
    let w = where();
    if (!w) return;
    if (!w.collapsed) {
      if (key !== 'Enter' && key !== 'Tab') return;  // a selection: the browser takes it out
      document.execCommand('delete');
      sync();
      w = where();
    }
    const b = blocks()[w.n], us = units(b.runs);
    if (key === 'Enter') {  // a new paragraph: after a heading, text; else of the same style
      e.preventDefault();
      const tail = compact(us.slice(w.k));
      b.runs = compact(us.slice(0, w.k));
      const style = tail.length ? b.style : HEADS.has(b.style) || ['sign', 'signatures', 'qr'].includes(b.style) ? 'text' : b.style;
      blocks().splice(w.n + 1, 0, {type: 'p', style, runs: tail, ...(tail.length && b.when ? {when: b.when} : {})});
      return redo(w.n + 1, 0);
    }
    if (key === 'Tab') {  // the line divided: a term and its value, place and who issues, one signature and the next
      e.preventDefault();
      if (HEADS.has(b.style) || b.style === 'qr') return;
      us.splice(w.k, 0, {t: '\t'});
      b.runs = compact(us);
      if (!PARTS.has(b.style)) b.style = 'fact';
      return redo(w.n, w.k + 1);
    }
    if (key === 'Backspace' && w.k > 0 && us[w.k - 1].t === '\t') {  // a part joined to the one before
      e.preventDefault();
      us.splice(w.k - 1, 1);
      b.runs = compact(us);
      return redo(w.n, w.k - 1);
    }
    if (key === 'Backspace' && w.k === 0 && w.n > 0) {  // into the paragraph before
      e.preventDefault();
      const prev = blocks()[w.n - 1];
      if (prev.type !== 'p') {
        if (!b.runs.length) blocks().splice(w.n, 1);
        return redo(Math.max(0, w.n - (b.runs.length ? 0 : 1)), 0);
      }
      const k = units(prev.runs).length;
      prev.runs = compact([...units(prev.runs), ...us]);
      blocks().splice(w.n, 1);
      return redo(w.n - 1, k);
    }
    if (key === 'Delete' && w.k < us.length && us[w.k].t === '\t') {
      e.preventDefault();
      us.splice(w.k, 1);
      b.runs = compact(us);
      return redo(w.n, w.k);
    }
    if (key === 'Delete' && w.k === us.length && blocks()[w.n + 1]?.type === 'p') {  // the next paragraph into this one
      e.preventDefault();
      b.runs = compact([...us, ...units(blocks()[w.n + 1].runs)]);
      blocks().splice(w.n + 1, 1);
      return redo(w.n, w.k);
    }
  });
  node.addEventListener('paste', e => {  // only the words; a blank line between them makes paragraphs
    e.preventDefault();
    const text = e.clipboardData.getData('text/plain').replace(/\r\n?/g, '\n');
    if (e.target.closest?.('[data-cell]')) return document.execCommand('insertText', false, text.replace(/\n+/g, ' '));
    let w = where();
    if (!w) return;
    if (!w.collapsed) {
      document.execCommand('delete');
      sync();
      w = where();
    }
    const b = blocks()[w.n], us = units(b.runs), rest = us.slice(w.k), parts = text.split(/\n{2,}/);
    b.runs = compact([...us.slice(0, w.k), ...units([{t: parts[0]}])]);
    const added = parts.slice(1).map(t => ({type: 'p', style: PARTS.has(b.style) || HEADS.has(b.style) ? 'text' : b.style, runs: [{t}]}));
    blocks().splice(w.n + 1, 0, ...added);
    const last = blocks()[w.n + added.length], k = units(last.runs).length;
    last.runs = compact([...units(last.runs), ...rest]);
    redo(w.n + added.length, k);
  });
  node.addEventListener('drop', e => e.preventDefault());  // nothing dragged in: what comes in comes typed or pasted
  node.addEventListener('click', e => {
    const p = e.target.closest('.flow-pill');
    picked = p && editable ? p : null;
    if (picked) at = +[...node.children].find(c => c.contains(picked)).dataset.n;  // its block: the cursor moving within it keeps it picked
    node.querySelectorAll('.flow-pill.picked').forEach(el => el.classList.remove('picked'));
    picked?.classList.add('picked');
    active = true;
    activated();
  });
  document.addEventListener('selectionchange', () => {
    const s = getSelection();
    if (!s.rangeCount || !node.contains(s.anchorNode)) return;
    saved = s.getRangeAt(0).cloneRange();
    const el = (s.anchorNode.nodeType === Node.ELEMENT_NODE ? s.anchorNode : s.anchorNode.parentElement)?.closest('[data-n]');
    const n = el && el.parentElement === node ? +el.dataset.n : null;
    if (n === at && active) return;
    at = n;
    if (!picked?.isConnected || !el?.contains(picked)) picked = null;
    active = true;
    activated();
  });

  // ---- values: from a menu where the cursor stands, by whose they are
  function values() {
    const live = getSelection().rangeCount && getSelection().getRangeAt(0);
    const range = live && node.contains(live.startContainer) ? live.cloneRange() : saved;  // where the cursor is, else where it was before the toolbar took it
    if (!range || !node.contains(range.startContainer)) return;
    const cell = (range.startContainer.nodeType === Node.ELEMENT_NODE ? range.startContainer : range.startContainer.parentElement)?.closest('[data-cell]');
    const list = cell ? listOf(+cell.dataset.cell.split('.')[0]) : null, row = list ? data.lists[list].rows[0] : null;
    const groups = {};
    if (list) groups[`Je Zeile: ${data.lists[list].label}`] = data.lists[list].fields.map(([key, label]) => [key, label, valueOf(key, row)]);
    data.fields.forEach(([key, label, group]) => (groups[group] ||= []).push([key, label, valueOf(key)]));
    W.menu(range, Object.entries(groups), key => {
      const host = cell || node;
      host.focus();
      getSelection().removeAllRanges();
      getSelection().addRange(range);
      if (!key) return;
      W.insert(range, W.pill({f: key, pre: ''}, valueOf(key, row), labelOf(key, list)));
      host.dispatchEvent(new Event('input', {bubbles: true}));
    });
  }

  // ---- the toolbar over the page, while the content is written
  function tools() {  // always there, so nothing moves; usable while the content is written
    if (!toolbar) return;
    const b = blocks()[at], on = editable && active && b ? '' : ' disabled';
    const styles = Object.entries(data.styles).filter(([key]) => key !== 'qr' || 'pruefadresse' in fields);
    toolbar.innerHTML = `<select data-tool="style" title="Stil des Absatzes"${b?.type === 'p' && !on ? '' : ' disabled'}>${styles.map(([key, [label]]) => option(key, label, b?.style || 'text')).join('')}</select>` +
      `<button type="button" data-tool="bold" title="Fett (Strg+B)"${on}><b>B</b></button>` +
      `<button type="button" data-tool="value" title="Wert einsetzen, wo die Schreibmarke steht; auch mit {"${on}><b>{ }</b> Wert</button>` +
      (Object.keys(data.lists).length ? `<button type="button" data-tool="table" title="Eine Tabelle einer Liste darunter einfügen"${on}>Tabelle</button>` : '') +
      `<span class="paper-format-hint">${on ? 'In den Inhalt klicken, um zu schreiben' : ''}</span>`;
  }
  toolbar?.addEventListener('mousedown', e => { if (e.target.closest('button')) e.preventDefault(); });  // the cursor stays where it is
  toolbar?.addEventListener('click', e => {
    const tool = e.target.closest('[data-tool]')?.dataset.tool;
    if (tool === 'bold') document.execCommand('bold');
    if (tool === 'value') values();
    if (tool === 'table') {
      const n = at == null ? blocks().length : at + 1, list = Object.keys(data.lists)[0];
      blocks().splice(n, 0, {type: 'table', list, columns: data.lists[list].fields.slice(0, 2).map(([key, label]) => ({head: [{t: label}], cell: [{f: key, pre: ''}], right: false}))});
      at = n;
      render();
      step();
      activated();
    }
  });
  toolbar?.addEventListener('change', e => {
    const b = blocks()[at];
    if (e.target.dataset.tool !== 'style' || b?.type !== 'p') return;
    const w = saved && where(saved);
    b.style = e.target.value;
    if (!PARTS.has(b.style)) b.runs = compact(units(b.runs).map(u => u.t === '\t' ? {...u, t: ' '} : u));  // a Tab only divides parts
    redo(at, w?.n === at ? w.k : 0);
  });

  // ---- the rail: what is picked
  function panelHtml() {
    const b = blocks()[at];
    if (picked) return valuePanel(b);
    if (!b) return '';
    if (b.type === 'table') {
      return `<h3>Tabelle</h3><div><label>Liste</label><select data-flow="list">${Object.entries(data.lists).map(([key, l]) => option(key, l.label, b.list)).join('')}</select></div>` +
        `<ul class="flow-columns">${b.columns.map((c, i) => `<li><span>${esc(said(c.head) || c.head.map(r => r.t ?? labelOf(r.f, b.list)).join('') || `Spalte ${i + 1}`)}</span>` +
          `<label class="option"><input type="checkbox" data-flow="right" data-i="${i}"${c.right ? ' checked' : ''}> rechtsbündig</label>` +
          `<button type="button" class="link" data-flow="uncolumn" data-i="${i}">entfernen</button></li>`).join('')}</ul>` +
        `<div class="actions"><button type="button" class="btn ghost small" data-flow="column">Spalte hinzufügen</button></div>${whenPick(b)}` +
        '<div class="actions"><button type="button" class="link" data-flow="remove">Tabelle entfernen</button></div>';
    }
    const [label, about] = data.styles[b.style];
    return `<h3>${esc(label)}</h3><p class="muted paper-hint"><small>${esc(about)}</small></p>${whenPick(b)}` +
      '<p class="muted paper-hint"><small><b>{</b> setzt einen Wert ein · <b>Tab</b> teilt die Zeile · <b>Umschalt+Enter</b> eine neue Zeile im Absatz</small></p>';
  }
  function valuePanel(b) {
    const key = picked.dataset.f, list = b?.type === 'table' ? b.list : null, row = list ? data.lists[list].rows[0] : null;
    const value = valueOf(key, row), worded = data.worded?.[key];
    return `<p class="paper-crumb"><button type="button" class="link" data-flow="unpick"><i class="arrow left"></i> ${b?.type === 'table' ? 'Tabelle' : 'Absatz'}</button></p>` +
      `<h3>${esc(labelOf(key, list))}</h3><dl class="flow-about"><dt>Aus</dt><dd>${esc(list ? `${data.lists[list].label}, je Zeile` : fields[key]?.group || '')}</dd>` +
      (worded ? '' : `<dt>Hier</dt><dd>${value ? `<span class="flow-pill">${W.brs(value)}</span>` : 'leer: entfällt mit dem, was davor steht'}</dd>`) + '</dl>' +
      (worded ? `<section class="paper-section"><h4>Wortlaut</h4><div class="flow-words" data-worded="${esc(key)}" contenteditable="true"></div>` +
        `<p class="muted paper-hint"><small>Einmal formuliert, für alle ${worded.uses} Dokumente, in denen er steht.</small></p></section>` : '') +
      `<section class="paper-section"><div><label>Davor, nur wenn gegeben</label><input data-flow="pre" value="${esc(picked.dataset.pre)}" placeholder="z. B. geb. "></div>` +
      `<label class="option"><input type="checkbox" data-flow="bold"${picked.classList.contains('b') ? ' checked' : ''}> Fett</label>` +
      '<div class="actions"><button type="button" class="link" data-flow="drop">Wert entfernen</button></div></section>';
  }
  function mounted() {  // the panel drawn: a Hochschule's text, to word where it stands
    const box = panel.querySelector('[data-worded]');
    if (!box) return;
    const worded = data.worded[box.dataset.worded];
    box.innerHTML = W.shown(W.runs(texts()[worded.text] ?? worded.body), k => worded.values[k], k => worded.labels[k] || k);
  }
  const fill = (body, found) => body.split('\n\n').filter(p => { const names = [...p.matchAll(/\{(\w+)\}/g)].map(m => m[1]); return !names.length || names.some(n => found[n]); })
    .join('\n\n').replace(/\{(\w+)\}/g, (_, n) => found[n] ?? '');
  const hostOf = el => el.closest('[data-cell]') || el.closest('p[data-n]');
  function reread(host, typed = false) {  // `typed`: the lead-in, one step however much is typed
    if (host.dataset.cell) {
      const [n, i, part] = host.dataset.cell.split('.');
      blocks()[+n].columns[+i][part] = W.read(host);
    } else sync();
    typed ? changed('flow:pre') : step();
  }
  panel.addEventListener('click', e => {
    const act = e.target.closest('[data-flow]')?.dataset.flow, b = blocks()[at];
    if (!act || e.target.matches('select, input')) return;
    if (act === 'unpick') { picked = null; return activated(); }
    if (act === 'drop' && picked) {
      const host = hostOf(picked);
      picked.remove();
      picked = null;
      reread(host);
      return activated();
    }
    if (!b) return;
    if (act === 'remove') { blocks().splice(at, 1); at = null; }
    if (act === 'column') {  // with the list's next value not yet shown, headed by its name
      const used = new Set(b.columns.flatMap(c => c.cell.filter(r => 'f' in r).map(r => r.f)));
      const [key, label] = data.lists[b.list].fields.find(([key]) => !used.has(key)) || [];
      b.columns.push({head: [{t: label || 'Spalte'}], cell: key ? [{f: key, pre: ''}] : [], right: false});
    }
    if (act === 'uncolumn') b.columns.splice(+e.target.dataset.i, 1);
    render();
    step();
    activated();
  });
  panel.addEventListener('change', e => {
    const t = e.target, act = t.dataset.flow, b = blocks()[at];
    if (act === 'bold' && picked) {
      picked.classList.toggle('b', t.checked);
      return reread(hostOf(picked));
    }
    if (!b || !['when', 'list', 'right'].includes(act)) return;
    if (act === 'when') t.value ? b.when = t.value : delete b.when;
    if (act === 'list') b.list = t.value;
    if (act === 'right') b.columns[+t.dataset.i].right = t.checked;
    const w = saved && where(saved);
    render();
    if (w) place(w.n, w.k);
    step();
    activated();
  });
  panel.addEventListener('input', e => {
    const t = e.target;
    if (t.dataset.flow === 'pre' && picked) {
      picked.dataset.pre = t.value;
      const host = hostOf(picked), list = host.dataset.cell ? listOf(+host.dataset.cell.split('.')[0]) : null;
      const value = valueOf(picked.dataset.f, list ? data.lists[list].rows[0] : null);
      if (value) picked.innerHTML = W.inner(t.value, value);
      return reread(host, true);
    }
    const box = t.closest?.('[data-worded]');
    if (box) {  // the Hochschule's own text, for every document: its value here follows at once
      const key = box.dataset.worded, worded = data.worded[key], body = W.notation(W.read(box));
      texts()[worded.text] = body;
      data.values[key] = fill(body, worded.values);
      node.querySelectorAll(`.flow-pill[data-f="${CSS.escape(key)}"]`).forEach(p => { p.innerHTML = W.inner(p.dataset.pre, data.values[key]); });
      changed('texts');
    }
  });
  W.typing(panel, {bold: () => false});

  render();
  return {
    node, render, html, mounted, tools,
    get active() { return active; },
    set active(on) {
      active = on;
      if (!on) { at = null; picked = null; node.querySelectorAll('.flow-pill.picked').forEach(el => el.classList.remove('picked')); }
      tools();
    },
    panel: panelHtml,
    title: () => said(blocks().find(b => b.type === 'p' && HEADS.has(b.style))?.runs),  // as the following pages name the document
  };
};
