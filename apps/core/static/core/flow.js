// A document's content, written on the page itself (documents.compose; the template editor, paper.js, holds it in its
// text area). What is typed is the Hochschule's; what ConCampus knows of the case shows as a field, a tinted pill with
// its value for a made-up person, never as code. A list shows as a table with its rows. Beside the page, the rail offers
// what can be put in where the cursor stands, what the field picked does, and what the block does; there is nothing
// else to learn. The content is kept as blocks of runs, {t: text} or {f: field, pre: what stands before it}.
window.Flow = function Flow({data, get, changed, editable, panel, activated}) {
  const node = Object.assign(document.createElement('div'), {className: 'flow'});
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const brs = s => esc(s).replace(/\n/g, '<br>');
  const fields = Object.fromEntries(data.fields);
  let saved = null, current = null, chosen = null, active = false;  // where the cursor was; the block worded; the field picked

  const flow = () => get();
  const listOf = path => { const m = /^b\.(\d+)\.columns/.exec(path || ''); return m && flow().blocks[+m[1]].list; };
  const labelOf = (key, list) => data.lists[list]?.fields.find(f => f[0] === key)?.[1] || fields[key] || key;
  const valueOf = (key, row) => String((row && key in row ? row[key] : data.values[key]) ?? '');

  // ---- runs as the page shows them, and back
  function pill(r, row, list) {
    const value = valueOf(r.f, row), label = labelOf(r.f, list);
    return `<span class="flow-pill${value ? '' : ' empty'}${r.b ? ' b' : ''}" contenteditable="false" data-f="${esc(r.f)}" data-pre="${esc(r.pre || '')}"` +
      ` title="${esc(label)}${value ? '' : ' · leer: entfällt mit dem, was davor steht'}">${value ? brs((r.pre || '') + value) : esc(label)}</span>`;
  }
  function shown(runs, row, list) {
    const out = (runs || []).map(r => 'f' in r ? pill(r, row, list) : r.b ? `<b>${brs(r.t)}</b>` : brs(r.t)).join('');
    return out.endsWith('<br>') ? `${out}<br>` : out;  // a last empty line, as a browser shows one
  }
  function read(el) {
    const out = [];
    const put = (t, b) => {
      if (!t) return;
      const last = out[out.length - 1];
      if (last && 't' in last && !!last.b === !!b) last.t += t;
      else out.push(b ? {t, b: true} : {t});
    };
    (function walk(parent, b) {
      for (const n of parent.childNodes) {
        if (n.nodeType === Node.TEXT_NODE) put(n.data.replace(/ /g, ' '), b);
        else if (n.nodeName === 'BR') put('\n', b);
        else if (n.dataset?.f) out.push({f: n.dataset.f, pre: n.dataset.pre || '', ...(b || n.classList.contains('b') ? {b: true} : {})});
        else if (n.nodeType === Node.ELEMENT_NODE) {
          if (['DIV', 'P'].includes(n.nodeName) && out.length) put('\n', b);
          walk(n, b || ['B', 'STRONG'].includes(n.nodeName));
        }
      }
    })(el, false);
    const last = out[out.length - 1];
    if (last && 't' in last && last.t.endsWith('\n')) {  // the browser's last line, as shown()
      last.t = last.t.slice(0, -1);
      if (!last.t) out.pop();
    }
    return out;
  }
  const hostOf = range => {  // the text the cursor stands in
    const el = range.startContainer.nodeType === Node.ELEMENT_NODE ? range.startContainer : range.startContainer.parentElement;
    return el?.closest('[data-path]');
  };
  const said = runs => (runs || []).map(r => 'f' in r ? valueOf(r.f) && (r.pre || '') + valueOf(r.f) : r.t).join('').trim();  // as text, filled in
  const at = path => path.split('.').slice(0, -1).reduce((o, k) => o[k === 'b' ? 'blocks' : k], flow());
  const set = (path, value) => { at(path)[path.split('.').pop()] = value; };
  const ed = path => `data-path="${path}"${editable ? ' contenteditable="true"' : ''}`;

  // ---- the blocks on the page
  function block(b, n) {
    const p = `b.${n}`;
    const list = data.lists[b.list];
    const inner = {
      heading: () => `<h1 ${ed(`${p}.runs`)}>${shown(b.runs)}</h1>`,
      subheading: () => `<h2 ${ed(`${p}.runs`)}>${shown(b.runs)}</h2>`,
      paragraph: () => `<div class="flow-text${b.small ? ' small' : ''}" ${ed(`${p}.runs`)}>${shown(b.runs)}</div>`,
      qr: () => `<div class="flow-text small flow-qr" ${ed(`${p}.runs`)}>${shown(b.runs)}</div>`,
      facts: () => `<dl class="facts">${b.rows.map(([l, v], i) => `<dt ${ed(`${p}.rows.${i}.0`)}>${shown(l)}</dt><dd ${ed(`${p}.rows.${i}.1`)}>${shown(v)}</dd>`).join('')}</dl>`,
      table: () => `<table><thead><tr>${b.columns.map((c, i) => `<th${c.right ? ' class="num"' : ''} ${ed(`${p}.columns.${i}.head`)}>${shown(c.head)}</th>`).join('')}</tr></thead><tbody>` +
        (list?.rows.length ? list.rows : [{}]).map((row, r) => `<tr${r ? ' class="flow-sample"' : ''}>${b.columns.map((c, i) => r
          ? `<td${c.right ? ' class="num"' : ''}>${shown(c.cell, row, b.list)}</td>`
          : `<td${c.right ? ' class="num"' : ''} ${ed(`${p}.columns.${i}.cell`)}>${shown(c.cell, row, b.list)}</td>`).join('')}</tr>`).join('') + '</tbody></table>',
      sign: () => `<div class="sign"><span ${ed(`${p}.left`)}>${shown(b.left)}</span><span ${ed(`${p}.right`)}>${shown(b.right)}</span></div>`,
      signatures: () => `<div class="signatures">${b.names.map((name, i) => `<span ${ed(`${p}.names.${i}`)}>${shown(name)}</span>`).join('')}</div>`,
    }[b.type]();
    const off = b.when && !valueOf(b.when);
    return `<div class="flow-block${n === current ? ' on' : ''}${off ? ' off' : ''}" data-n="${n}"${b.when ? ` title="Nur, wo ${esc(labelOf(b.when))} gegeben ist${off ? ': hier nicht' : ''}"` : ''}>${inner}</div>`;
  }
  function render() {
    node.innerHTML = flow().blocks.map(block).join('') || '<p class="flow-empty">Noch kein Inhalt: rechts einen Block hinzufügen.</p>';
  }
  function html() {  // read-only, for the following pages
    const was = editable;
    editable = false;
    const out = flow().blocks.map(block).join('');
    editable = was;
    return out;
  }

  // ---- typing
  node.addEventListener('input', e => {
    const el = e.target.closest('[data-path]');
    if (el) {
      set(el.dataset.path, read(el));
      changed();
    }
  });
  node.addEventListener('keydown', e => {
    const el = e.target.closest('[data-path]');
    if (!el) return;
    if (e.key === 'Enter') {
      e.preventDefault();
      if (!el.matches('h1, h2, th')) document.execCommand('insertLineBreak');  // a heading keeps to one line
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
      e.preventDefault();
      document.execCommand('bold');
    }
  });
  node.addEventListener('paste', e => {  // only the words: no formatting comes in with them
    e.preventDefault();
    document.execCommand('insertText', false, e.clipboardData.getData('text/plain'));
  });
  node.addEventListener('focusin', e => {
    const n = +e.target.closest('[data-n]')?.dataset.n;
    if (n !== current || !active) {
      current = n;
      chosen = null;
      node.querySelectorAll('.flow-block').forEach(el => el.classList.toggle('on', +el.dataset.n === n));
      active = true;
      activated();
    }
  });
  node.addEventListener('click', e => {
    const p = e.target.closest('.flow-pill');
    if (!p || !editable) return;
    chosen = p;
    node.querySelectorAll('.flow-pill.picked').forEach(el => el.classList.remove('picked'));
    p.classList.add('picked');
    active = true;
    activated();
  });
  document.addEventListener('selectionchange', () => {
    const s = getSelection();
    if (s.rangeCount && (node.contains(s.anchorNode) || panel.querySelector('[data-path="office"]')?.contains(s.anchorNode))) saved = s.getRangeAt(0).cloneRange();
  });

  // ---- the rail, while the content is worded
  const option = (value, label, now) => `<option value="${esc(value)}"${value === now ? ' selected' : ''}>${esc(label)}</option>`;
  const btn = (act, label, title = '') => `<button type="button" class="btn ghost small" data-flow="${act}"${title ? ` title="${esc(title)}"` : ''}>${label}</button>`;
  function fieldList(list) {
    const row = list ? data.lists[list] : null;
    const group = (title, entries, rowValues) => `<p class="flow-group"><small>${esc(title)}</small></p><div class="flow-fields">` + entries.map(([key, label]) => {
      const value = valueOf(key, rowValues);
      return `<button type="button" data-flow="insert" data-key="${esc(key)}" title="Einsetzen, wo die Schreibmarke steht"><span>${esc(label)}</span><small>${esc(value || 'leer')}</small></button>`;
    }).join('') + '</div>';
    return (row ? group(`Je Zeile: ${row.label}`, row.fields, row.rows[0]) : '') + group('Zum Dokument', data.fields, null);
  }
  function panelHtml() {
    const content = flow(), b = content.blocks[current];
    let out = `<h3>Inhalt</h3><div class="flow-office"><label>Absender, oben rechts</label><div class="flow-line" ${ed('office')}>${shown(content.office)}</div></div>`;
    if (chosen) {
      const key = chosen.dataset.f, list = listOf(chosen.closest('[data-path]')?.dataset.path);
      out += `<section class="paper-section"><h4>Feld: ${esc(labelOf(key, list))}</h4>` +
        `<p class="muted"><small>Hier: ${esc(valueOf(key, list ? data.lists[list].rows[0] : null) || 'leer')}. Ist es leer, entfällt es mit dem, was davor steht; steht nichts davor, entfällt die ganze Zeile.</small></p>` +
        `<div><label>Davor, nur wenn es gegeben ist</label><input data-flow="pre" value="${esc(chosen.dataset.pre)}" placeholder="z. B. geb. "></div>` +
        `<label class="option"><input type="checkbox" data-flow="bold"${chosen.classList.contains('b') ? ' checked' : ''}> Fett</label>` +
        `<div class="actions">${btn('unpick', 'Fertig')}<button type="button" class="link" data-flow="drop">Feld entfernen</button></div></section>`;
    }
    out += `<section class="paper-section"><h4>Daten einsetzen</h4>${fieldList(saved && listOf(hostOf(saved)?.dataset.path))}</section>`;
    if (b) {
      const [label] = data.blocks[b.type], last = content.blocks.length - 1;
      out += `<section class="paper-section"><h4>${esc(label)}</h4><div class="flow-acts">${current ? btn('up', '↑', 'Nach oben') : ''}${current < last ? btn('down', '↓', 'Nach unten') : ''}<button type="button" class="link" data-flow="remove">Entfernen</button></div>` +
        `<div><label>Zeigen</label><select data-flow="when">${option('', 'immer', b.when || '')}${data.fields.map(([key, l]) => option(key, `nur, wo ${l} gegeben ist`, b.when || '')).join('')}</select></div>` +
        (b.type === 'paragraph' ? `<label class="option"><input type="checkbox" data-flow="small"${b.small ? ' checked' : ''}> Klein und grau, als Hinweis</label>` : '') +
        (b.type === 'facts' ? `<div class="actions">${btn('row', 'Zeile hinzufügen')}${b.rows.length > 1 ? btn('unrow', 'Letzte Zeile entfernen') : ''}</div>` : '') +
        (b.type === 'signatures' ? `<div class="actions">${btn('name', 'Unterschrift hinzufügen')}${b.names.length > 1 ? btn('unname', 'Letzte entfernen') : ''}</div>` : '') +
        (b.type === 'table' ? `<div><label>Liste</label><select data-flow="list">${Object.entries(data.lists).map(([key, l]) => option(key, l.label, b.list)).join('')}</select></div>` +
          b.columns.map((c, i) => `<div class="flow-column"><span>${esc(c.head.map(r => r.t ?? labelOf(r.f, b.list)).join('') || `Spalte ${i + 1}`)}</span>` +
            `<label class="option"><input type="checkbox" data-flow="right" data-i="${i}"${c.right ? ' checked' : ''}> rechtsbündig</label><button type="button" class="link" data-flow="uncolumn" data-i="${i}">entfernen</button></div>`).join('') +
          `<div class="actions">${btn('column', 'Spalte hinzufügen')}</div>` : '') + '</section>';
    }
    const kinds = Object.keys(data.blocks).filter(kind => (kind !== 'table' || Object.keys(data.lists).length) && (kind !== 'qr' || 'pruefadresse' in fields));
    return out + `<section class="paper-section"><h4>${b ? 'Darunter einfügen' : 'Einfügen'}</h4><div class="flow-new">${kinds.map(kind => btn(`add-${kind}`, esc(data.blocks[kind][0]), data.blocks[kind][1])).join('')}</div></section>`;
  }
  const FRESH = {
    heading: () => ({runs: [{t: 'Titel'}]}), subheading: () => ({runs: [{t: 'Abschnitt'}]}), paragraph: () => ({runs: [{t: 'Text'}]}),
    facts: () => ({rows: [[[{t: 'Bezeichnung'}], []]]}), table: () => ({list: Object.keys(data.lists)[0], columns: [{head: [{t: 'Spalte'}], cell: [], right: false}]}),
    sign: () => ({left: [{f: 'ort', pre: ''}, {t: ', '}, {f: 'datum', pre: ''}], right: [{f: 'amt', pre: ''}]}),
    signatures: () => ({names: [[{t: 'Name'}]]}), qr: () => ({runs: [{t: 'Zu prüfen unter '}, {f: 'pruefadresse', pre: ''}]}),
  };
  function again(focus = current) {  // the blocks drawn anew, the cursor back in the block it was in
    render();
    changed();
    activated();
    node.querySelector(`[data-n="${focus}"] [data-path]`)?.focus();
  }
  function insert(key) {
    const range = saved, host = range && hostOf(range);
    if (!host) return;
    const list = listOf(host.dataset.path), row = list ? data.lists[list].rows[0] : null;
    const holder = document.createElement('span');
    holder.innerHTML = pill({f: key, pre: ''}, row, list);
    const made = holder.firstChild;
    range.deleteContents();
    const before = range.startContainer.nodeType === Node.TEXT_NODE ? range.startContainer.data.slice(0, range.startOffset) : '';
    if (before && !/\s$/.test(before)) range.insertNode(document.createTextNode(' '));  // a word apart from the one before it
    range.collapse(false);
    range.insertNode(made);
    range.setStartAfter(made);
    range.collapse(true);
    set(host.dataset.path, read(host));
    changed();
    host.focus();
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  }
  panel.addEventListener('click', e => {
    const b = e.target.closest('[data-flow]');
    if (!b || !active || b.tagName === 'SELECT' || b.tagName === 'INPUT') return;
    const content = flow(), now = content.blocks[current], act = b.dataset.flow, i = +b.dataset.i;
    if (act === 'insert') return insert(b.dataset.key);
    if (act === 'unpick') { chosen = null; return activated(); }
    if (act === 'drop' && chosen) {
      const host = chosen.closest('[data-path]');
      chosen.remove();
      chosen = null;
      set(host.dataset.path, read(host));
      changed();
      return activated();
    }
    if (act.startsWith('add-')) {
      const kind = act.slice(4), at = current == null ? content.blocks.length : current + 1;
      content.blocks.splice(at, 0, {type: kind, ...FRESH[kind]()});
      current = at;
      return again(at);
    }
    if (!now) return;
    ({
      up: () => { content.blocks.splice(current - 1, 0, ...content.blocks.splice(current, 1)); current -= 1; },
      down: () => { content.blocks.splice(current + 1, 0, ...content.blocks.splice(current, 1)); current += 1; },
      remove: () => { content.blocks.splice(current, 1); current = null; },
      row: () => now.rows.push([[{t: 'Bezeichnung'}], []]), unrow: () => now.rows.pop(),
      name: () => now.names.push([{t: 'Name'}]), unname: () => now.names.pop(),
      column: () => now.columns.push({head: [{t: 'Spalte'}], cell: [], right: false}), uncolumn: () => now.columns.splice(i, 1),
    })[act]?.();
    again();
  });
  panel.addEventListener('change', e => {
    const t = e.target, act = t.dataset.flow, now = flow().blocks[current];
    if (!act || !active) return;
    if (act === 'bold' && chosen) {
      chosen.classList.toggle('b', t.checked);
      const host = chosen.closest('[data-path]');
      set(host.dataset.path, read(host));
      return changed();
    }
    if (!now) return;
    if (act === 'when') t.value ? now.when = t.value : delete now.when;
    if (act === 'small') t.checked ? now.small = true : delete now.small;
    if (act === 'list') now.list = t.value;
    if (act === 'right') now.columns[+t.dataset.i].right = t.checked;
    again();
  });
  panel.addEventListener('input', e => {
    const t = e.target;
    if (t.dataset.flow === 'pre' && chosen) {
      chosen.dataset.pre = t.value;
      const host = chosen.closest('[data-path]'), list = listOf(host.dataset.path), key = chosen.dataset.f;
      const value = valueOf(key, list ? data.lists[list].rows[0] : null);
      if (value) chosen.textContent = t.value + value;
      set(host.dataset.path, read(host));
      changed();
    } else if (t.dataset.path === 'office') {
      flow().office = read(t);
      changed();
    }
  });

  render();
  return {
    node, render, html,
    get active() { return active; },
    set active(on) { active = on; if (!on) { current = null; chosen = null; node.querySelectorAll('.flow-block.on, .flow-pill.picked').forEach(el => el.classList.remove('on', 'picked')); } },
    panel: panelHtml,
    office: () => said(flow().office),  // as the letterhead prints it
    title: () => said(flow().blocks.find(b => b.type === 'heading')?.runs),  // as the following pages name the document
  };
};
