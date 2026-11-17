// A document's content, written where it prints (documents.compose; paper.js holds it in the text area). It reads as
// the document does: paragraphs, each in a style, and tables of a list. What ConCampus fills in is a placeholder, a
// tinted pill with its name; the preview fills it in. Enter, Backspace, Tab and Ctrl+B do what they do in a word
// processor. The rail holds the tools: a paragraph's style, bold, a placeholder from a menu (also at {, or dragged from
// it into the text), a table; of what is picked it says: a placeholder, what stands before it; a paragraph,
// when it shows; a table, its list and columns. Words are kept as runs (words.js).
window.Flow = function Flow({data, get, texts, changed, editable, panel, activated, undo}) {
  const W = window.Words, esc = W.esc;
  const PARTS = new Set(['fact', 'sign', 'signatures']), HEADS = new Set(['title', 'heading']);  // a Tab divides the lines of PARTS
  const fields = Object.fromEntries(data.fields.map(([key, label, group]) => [key, {label, group}]));
  const node = Object.assign(document.createElement('div'), {className: 'flow', lang: 'de', spellcheck: false});  // a browser checking German against English marks every word
  let at = null, picked = null, saved = null, active = false, steps = 0;  // the block the cursor is in; the placeholder picked; where the cursor was
  const step = () => changed(`flow#${++steps}`);  // a change of its own to undo; typing in between is one

  const blocks = () => get().blocks;
  const listOf = n => blocks()[n]?.type === 'table' ? blocks()[n].list : null;
  const labelOf = (key, list) => data.lists[list]?.fields.find(f => f[0] === key)?.[1] || fields[key]?.label || key;
  const valueOf = (key, list) => String((list && key in (data.lists[list].rows[0] || {}) ? data.lists[list].rows[0][key] : data.values[key]) ?? '');  // its example
  const option = (value, label, now) => `<option value="${esc(value)}"${value === now ? ' selected' : ''}>${esc(label)}</option>`;
  const whenPick = b => `<div><label>Zeigen</label><select data-flow="when">${option('', 'immer', b.when || '')}${data.fields.map(([key, label]) => option(key, `nur mit ${label}`, b.when || '')).join('')}</select></div>`;

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

  // ---- on the page; the Hochschule's own texts (data.worded) written where they stand, for every document they stand in
  const shown = (runs, list) => W.shown(runs, key => labelOf(key, list), key => valueOf(key, list), r => data.worded?.[r.f] ? own(r) : null);
  const ownWords = key => { const w = data.worded[key]; return W.shown(W.runs(texts()[w.text] ?? w.body), k => w.labels[k] || k, k => w.values[k]); };
  const own = r => `<span class="flow-pill flow-own${r.b ? ' b' : ''}" contenteditable="false" data-f="${esc(r.f)}" data-pre="${esc(r.pre || '')}">` +
    `${r.pre ? `<span class="flow-pre">${W.brs(r.pre)}</span>` : ''}<span class="flow-words" data-worded="${esc(r.f)}" contenteditable="${editable}">${ownWords(r.f)}</span></span>`;
  const condition = b => b.when ? ` data-when="${esc(b.when)}" data-if title="${esc(`Nur mit ${labelOf(b.when)}`)}"` : '';  // marked in the margin, said on hover
  function paragraph(b, n) {
    const inner = PARTS.has(b.style) ? cells(b.runs).map(c => `<span class="flow-cell">${shown(c) || '<br>'}</span>`).join('') : shown(b.runs) || '<br>';
    return `<p class="flow-p s-${b.style}" data-n="${n}" data-style="${b.style}"${condition(b)}>${inner}</p>`;
  }
  function table(b, n) {  // its headings and one row, as every row of the list is set; that more follow, hinted beneath
    const cell = (tag, c, i, part) => `<${tag}${c.right ? ' class="num"' : ''} data-cell="${n}.${i}.${part}"${editable ? ' contenteditable="true"' : ''}>${shown(c[part], b.list) || '<br>'}</${tag}>`;
    return `<div class="flow-table" contenteditable="false" data-n="${n}"${condition(b)}><table><thead><tr>${b.columns.map((c, i) => cell('th', c, i, 'head')).join('')}</tr></thead>` +
      `<tbody><tr>${b.columns.map((c, i) => cell('td', c, i, 'cell')).join('')}</tr><tr class="flow-more"><td colspan="${b.columns.length}">⋮</td></tr></tbody></table></div>`;
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
    const words = e.target.closest?.('[data-worded]'), cell = e.target.closest?.('[data-cell]');
    if (words) {  // the Hochschule's own text: wherever else it stands here follows at once
      const key = words.dataset.worded;
      texts()[data.worded[key].text] = W.notation(W.read(words));
      node.querySelectorAll(`[data-worded="${CSS.escape(key)}"]`).forEach(other => { if (other !== words) other.innerHTML = ownWords(key); });
      return changed('texts');
    }
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
    const words = e.target.closest?.('[data-worded]');
    if (words) {  // in the Hochschule's own text: its placeholders at {, a new line at Enter, no bold
      if (key === '{' || key === 'Enter' || key === 'Tab' || mod && key.toLowerCase() === 'b') e.preventDefault();
      if (key === '{') values();
      if (key === 'Enter') document.execCommand('insertLineBreak');
      return;
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
    if (e.target.closest?.('[data-cell], [data-worded]')) return document.execCommand('insertText', false, text.replace(/\n+/g, ' '));
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
  node.addEventListener('drop', e => { if (!e.dataTransfer.types.includes('application/x-platzhalter')) e.preventDefault(); });  // nothing else dragged in: what comes in comes typed or pasted
  // A click beside the words, between two paragraphs or the parts of one: the cursor into the line nearest to it, as a
  // word processor puts it; the browser would leave it between them, where nothing is written.
  function nearest(x, y) {
    const off = (r, at, a, b) => Math.max(r[a] - at, 0, at - r[b]);
    const [r] = [...node.querySelectorAll(':scope > .flow-p > .flow-cell, :scope > .flow-p:not(:has(> .flow-cell))')].map(el => el.getBoundingClientRect())
      .sort((p, q) => off(p, y, 'top', 'bottom') - off(q, y, 'top', 'bottom') || off(p, x, 'left', 'right') - off(q, x, 'left', 'right'));
    const range = r && document.caretRangeFromPoint?.(Math.min(Math.max(x, r.left + 1), r.right - 1), Math.min(Math.max(y, r.top + 1), r.bottom - 1));
    if (!range) return;
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  }
  node.addEventListener('click', e => {
    if (!editable) return;  // shown on a Vorlage, not written there
    if (e.detail === 3) {  // three clicks: the paragraph, or the part of it, and no further, so typing over it keeps the next
      const part = e.target.closest('.flow-cell, [data-cell], .flow-words') || e.target.closest('[data-n]');
      if (part && node.contains(part)) getSelection().selectAllChildren(part);
    }
    if (e.target === node || e.target.matches('.flow-p:has(> .flow-cell)')) nearest(e.clientX, e.clientY);
    const p = e.target.closest('.flow-own') || e.target.closest('.flow-pill');  // in the Hochschule's own text: the text, not a value in it
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

  // ---- placeholders: what may stand where the cursor is, by whose they are; put in from the rail, or a menu there
  function offered() {
    const live = getSelection().rangeCount && getSelection().getRangeAt(0);
    const range = live && node.contains(live.startContainer) ? live.cloneRange() : saved;  // where the cursor is, else where it was before the toolbar took it
    if (!editable || !range || !node.contains(range.startContainer)) return null;
    const there = range.startContainer.nodeType === Node.ELEMENT_NODE ? range.startContainer : range.startContainer.parentElement;
    const words = there.closest('[data-worded]'), cell = there.closest('[data-cell]');
    if (words) {  // in the Hochschule's own text: those it may hold
      const w = data.worded[words.dataset.worded];
      return {range, host: words, groups: [[w.title, Object.entries(w.labels).map(([k, label]) => [k, label, w.values[k]])]], pill: k => W.pill({f: k, pre: ''}, w.labels[k] || k, w.values[k])};
    }
    const list = cell ? listOf(+cell.dataset.cell.split('.')[0]) : null, groups = {};
    if (list) groups[`Je Zeile: ${data.lists[list].label}`] = data.lists[list].fields.map(([key, label]) => [key, label, valueOf(key, list)]);
    data.fields.forEach(([key, label, group]) => group && (groups[group] ||= []).push([key, label, valueOf(key)]));  // none: whether a paragraph stands
    return {range, host: cell || node, groups: Object.entries(groups), pill: k => W.pill({f: k, pre: ''}, labelOf(k, list), valueOf(k, list))};
  }
  function put(o, key) {  // the cursor back where it was, the placeholder put in there
    o.host.focus();
    getSelection().removeAllRanges();
    getSelection().addRange(o.range);
    if (!key || !o.groups.some(([, entries]) => entries.some(([k]) => k === key))) return;
    W.insert(o.range, o.pill(key));
    o.host.dispatchEvent(new Event('input', {bubbles: true}));
  }
  function values(button) {
    const o = offered();
    if (o) W.menu(o.range, o.groups, key => put(o, key), button);
  }

  // ---- the rail, while the content is written: the tools at its top (the paragraph's style, bold, a placeholder, a
  // table), then what is picked: a paragraph, when it shows; a table, its list and columns; a placeholder, its lead-in
  const putting = '<button type="button" data-tool="value" title="Platzhalter einsetzen, wo die Schreibmarke steht; auch mit {"><b>{ }</b> Platzhalter</button>';
  function tools(b) {
    const styles = Object.entries(data.styles).filter(([key]) => key !== 'qr' || 'pruefadresse' in fields);
    return '<div class="paper-type">' + (b.type === 'p' ? `<select data-tool="style" title="Stil des Absatzes">${styles.map(([key, [label]]) => option(key, label, b.style)).join('')}</select>` : '') +
      `<button type="button" data-tool="bold" title="Fett (Strg+B)"><b>B</b></button>${putting}` +
      (Object.keys(data.lists).length ? '<button type="button" data-tool="table" title="Eine Tabelle einer Liste darunter einfügen">Tabelle</button>' : '') + '</div>';
  }
  panel.addEventListener('mousedown', e => { if (e.target.closest('button[data-tool]')) e.preventDefault(); });  // the cursor stays where it is
  panel.addEventListener('click', e => {
    const tool = active && e.target.closest('[data-tool]')?.dataset.tool;
    const anchor = getSelection().anchorNode;
    if (tool === 'bold' && !(anchor?.nodeType === Node.ELEMENT_NODE ? anchor : anchor?.parentElement)?.closest('[data-worded]')) document.execCommand('bold');  // the Hochschule's own texts are plain
    if (tool === 'value') values(e.target.closest('[data-tool]'));
    if (tool === 'table') {
      const n = at == null ? blocks().length : at + 1, list = Object.keys(data.lists)[0];
      blocks().splice(n, 0, {type: 'table', list, columns: data.lists[list].fields.slice(0, 2).map(([key, label]) => ({head: [{t: label}], cell: [{f: key, pre: ''}], right: false}))});
      at = n;
      render();
      step();
      activated();
    }
  });
  panel.addEventListener('change', e => {
    const b = blocks()[at];
    if (e.target.dataset.tool !== 'style' || b?.type !== 'p') return;
    const w = saved && where(saved);
    b.style = e.target.value;
    if (!PARTS.has(b.style)) b.runs = compact(units(b.runs).map(u => u.t === '\t' ? {...u, t: ' '} : u));  // a Tab only divides parts
    redo(at, w?.n === at ? w.k : 0);
  });
  W.dropping(node, (range, key) => {  // a placeholder dragged in from the menu: where it was let go
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    const o = offered();
    if (o) put(o, key);
  });

  const section = html => `<section class="paper-section">${html}</section>`;
  const drop = words => `<div class="actions"><button type="button" class="link" data-flow="drop">${words}</button></div>`;
  function panelHtml() {
    const b = blocks()[at];
    if (picked) return valuePanel(b);
    if (!b) return '';
    if (b.type === 'table') {
      return `<h3>Tabelle</h3>${tools(b)}<div><label>Liste</label><select data-flow="list">${Object.entries(data.lists).map(([key, l]) => option(key, l.label, b.list)).join('')}</select></div>` +
        `<ul class="flow-columns">${b.columns.map((c, i) => `<li><span>${esc(c.head.map(r => r.t ?? labelOf(r.f, b.list)).join('') || `Spalte ${i + 1}`)}</span>` +
          `<label class="option"><input type="checkbox" data-flow="right" data-i="${i}"${c.right ? ' checked' : ''}> rechtsbündig</label>` +
          `<button type="button" class="link" data-flow="uncolumn" data-i="${i}">entfernen</button></li>`).join('')}</ul>` +
        `<div class="actions"><button type="button" class="btn ghost small" data-flow="column">Spalte hinzufügen</button></div>${whenPick(b)}` +
        '<div class="actions"><button type="button" class="link" data-flow="remove">Tabelle entfernen</button></div>';
    }
    return `<h3>Absatz</h3>${tools(b)}${whenPick(b)}`;
  }
  function valuePanel(b) {
    const key = picked.dataset.f, list = b?.type === 'table' ? b.list : null, value = valueOf(key, list), worded = data.worded?.[key], there = data.elsewhere?.[key];
    const head = `<h3>${esc(labelOf(key, list))}</h3>`;
    if (worded) return head + (worded.uses > 1 ? `<p class="muted paper-hint">Ein Text für ${worded.uses} Dokumente</p>` : '') + `<div class="paper-type">${putting}</div>` + drop('Aus diesem Dokument entfernen');  // written where it stands
    if (there) return `${head}<p class="paper-hint">${esc(there.said)}</p>${there.url ? `<p><a href="${esc(there.url)}">${esc(there.link)} <i class="arrow"></i></a></p>` : ''}`;  // worded elsewhere: where
    return head + `<p class="muted paper-hint">${esc([list ? `Je Zeile: ${data.lists[list].label}` : fields[key]?.group, value && `z. B. ${value}`].filter(Boolean).join(' · '))}</p>` +
      `<div><label>Text davor</label><input data-flow="pre" value="${esc(picked.dataset.pre)}" placeholder="z. B. geb. "><small class="muted">Entfällt, wo der Platzhalter leer bleibt.</small></div>` +
      `<label class="option"><input type="checkbox" data-flow="bold"${picked.classList.contains('b') ? ' checked' : ''}> Fett</label>${drop('Entfernen')}`;
  }
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
      picked.innerHTML = W.inner(t.value, labelOf(picked.dataset.f, list), valueOf(picked.dataset.f, list));
      return reread(host, true);
    }
  });

  render();
  return {
    node, render, html,
    get active() { return active; },
    set active(on) {
      active = on;
      if (!on) { at = null; picked = null; node.querySelectorAll('.flow-pill.picked').forEach(el => el.classList.remove('picked')); }
    },
    panel: panelHtml,
  };
};
