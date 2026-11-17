// Words with values in them, written where they stand (flow.js, paper.js, documents/text.html): what is typed is the
// Hochschule's; what ConCampus fills in shows as a tinted pill with its value, an example for a made-up person, never
// as code. Words are kept as runs, {t: text} or {f: field, pre: what stands before it}, either {b: true} for bold; a
// text of core.texts, or of a Briefbogen, as its notation, {field} and **bold**.
window.Words = (() => {
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const brs = s => esc(s).replace(/\n/g, '<br>');

  function runs(words) {  // from the notation
    const out = [];
    let bold = false, at = 0;
    for (const m of String(words).matchAll(/\*\*|\{(\w+)\}/g)) {
      if (m.index > at) out.push({t: words.slice(at, m.index), ...(bold ? {b: true} : {})});
      if (m[0] === '**') bold = !bold;
      else out.push({f: m[1], pre: '', ...(bold ? {b: true} : {})});
      at = m.index + m[0].length;
    }
    if (at < words.length) out.push({t: words.slice(at), ...(bold ? {b: true} : {})});
    return out;
  }
  function notation(found) {  // and back: bold runs side by side under one pair of stars
    let out = '', bold = false;
    for (const r of found) {
      if (!!r.b !== bold) { out += '**'; bold = !bold; }
      out += 'f' in r ? `{${r.f}}` : r.t;
    }
    return bold ? `${out}**` : out;
  }

  function pill(r, value, label) {
    return `<span class="flow-pill${value ? '' : ' empty'}${r.b ? ' b' : ''}" contenteditable="false" data-f="${esc(r.f)}" data-pre="${esc(r.pre || '')}"` +
      ` title="${esc(label)}${value ? '' : ' · leer: entfällt mit dem, was davor steht'}">${value ? inner(r.pre, value) : esc(label)}</span>`;
  }
  // a pill's words: what stands before the value as it prints, untinted, so „Musik, Prüfungsordnung“ shows no gap
  const inner = (pre, value) => pre ? `<span class="flow-pre">${brs(pre)}</span><span class="flow-value">${brs(value)}</span>` : brs(value);
  function shown(found, valueOf, labelOf, pillOf = null) {  // pillOf: a field shown otherwise, as a text in a text (flow.js)
    const out = (found || []).map(r => 'f' in r ? pillOf?.(r) ?? pill(r, valueOf(r.f), labelOf(r.f)) : r.b ? `<b>${brs(r.t)}</b>` : brs(r.t)).join('');
    return out.endsWith('<br>') ? `${out}<br>` : out;  // a last empty line, as a browser shows one
  }
  function read(el, exact = false) {  // what is written in `el`, as runs; `exact`: up to a point, its last line break too
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
    if (!exact && last && 't' in last && last.t.endsWith('\n')) {  // the browser's last line, as shown()
      last.t = last.t.slice(0, -1);
      if (!last.t) out.pop();
    }
    return out;
  }

  // Typing in `node`: Enter a new line (where `oneLine` says so, none), Ctrl+B bold (where `bold` allows), pasted only words.
  function typing(node, {oneLine = () => false, bold = () => true} = {}) {
    node.addEventListener('keydown', e => {
      const host = e.target.closest?.('[contenteditable="true"]');
      if (!host || e.defaultPrevented) return;  // handled where it was typed
      if (e.key === 'Enter') {
        e.preventDefault();
        if (!oneLine(host)) document.execCommand('insertLineBreak');
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
        e.preventDefault();
        if (bold(host)) document.execCommand('bold');
      }
    });
    node.addEventListener('paste', e => {
      if (!e.target.closest?.('[contenteditable="true"]') || e.defaultPrevented) return;  // a field of its own pastes as it does
      e.preventDefault();
      document.execCommand('insertText', false, e.clipboardData.getData('text/plain'));
    });
  }
  // A pill put where the cursor was (`range`), a word apart from the one before it; the cursor after it.
  function insert(range, html) {
    const holder = document.createElement('span');
    holder.innerHTML = html;
    const made = holder.firstChild;
    range.deleteContents();
    const before = range.startContainer.nodeType === Node.TEXT_NODE ? range.startContainer.data.slice(0, range.startOffset) : '';
    if (before && !/\s$/.test(before)) range.insertNode(document.createTextNode(' '));
    range.collapse(false);
    range.insertNode(made);
    range.setStartAfter(made);
    range.collapse(true);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  }
  // The values to put in where the cursor stands, under it: by whose they are, each with its value; typing narrows them,
  // arrows and Enter pick one, Escape or a click elsewhere closes. groups: [[title, [[key, label, value]]]].
  const fold = s => s.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
  function menu(range, groups, pick) {
    document.querySelector('.words-menu')?.remove();
    const box = Object.assign(document.createElement('div'), {className: 'words-menu'});
    box.innerHTML = '<input type="search" placeholder="Wert suchen" aria-label="Wert suchen"><div></div>';
    document.body.append(box);
    const input = box.firstChild, list = box.lastChild, rect = range.getBoundingClientRect();
    Object.assign(box.style, {left: `${Math.max(8, Math.min(rect.left, innerWidth - 328))}px`, top: `${Math.min(rect.bottom + 6, innerHeight - 340)}px`});
    let found = [], on = 0;
    const show = () => {
      const q = fold(input.value.trim());
      found = [];
      list.innerHTML = groups.map(([title, entries]) => {
        const hits = entries.filter(([key, label]) => !q || fold(`${label} ${key}`).includes(q));
        return hits.length ? `<p><small>${esc(title)}</small></p>` + hits.map(([key, label, value]) => `<button type="button" data-n="${found.push(key) - 1}"><span>${esc(label)}</span><small>${esc(value || 'leer')}</small></button>`).join('') : '';
      }).join('') || '<p class="muted"><small>Kein Wert heißt so.</small></p>';
      on = Math.min(on, Math.max(0, found.length - 1));
      list.querySelectorAll('button').forEach(b => b.classList.toggle('on', +b.dataset.n === on));
      list.querySelector('button.on')?.scrollIntoView({block: 'nearest'});
    };
    const close = picked => {
      box.remove();
      document.removeEventListener('pointerdown', outside, true);
      pick(picked);
    };
    const outside = e => { if (!box.contains(e.target)) close(null); };
    input.addEventListener('input', () => { on = 0; show(); });
    input.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); on = (on + (e.key === 'ArrowDown' ? 1 : found.length - 1)) % Math.max(1, found.length); show(); }
      else if (e.key === 'Enter') { e.preventDefault(); if (found.length) close(found[on]); }
      else if (e.key === 'Escape') { e.preventDefault(); close(null); }
    });
    list.addEventListener('click', e => { const b = e.target.closest('button'); if (b) close(found[+b.dataset.n]); });
    document.addEventListener('pointerdown', outside, true);
    show();
    input.focus();
  }

  // A page of texts (documents/text.html): each [data-words] written in place, kept in the form's field it names; the
  // fields beside it go where the cursor was.
  function page(form) {
    const data = JSON.parse(document.getElementById(form.dataset.wordsPage).textContent);
    let saved = null;
    form.querySelectorAll('[data-words]').forEach(el => {
      const input = form.elements[el.dataset.words], text = data[el.dataset.text];
      el.innerHTML = shown(runs(input.value), f => text.values[f], f => text.labels[f] || f);
      el.contentEditable = 'true';
      el.addEventListener('input', () => {
        input.value = notation(read(el));
        input.dispatchEvent(new Event('input', {bubbles: true}));  // unsaved: leaving asks first (app.js)
      });
    });
    typing(form, {oneLine: host => host.matches('[data-one-line]'), bold: () => false});
    form.addEventListener('keydown', e => {  // { : a value, chosen from a menu where the cursor stands
      const host = e.target.closest?.('[data-words]');
      if (!host || e.key !== '{') return;
      e.preventDefault();
      const text = data[host.dataset.text], range = getSelection().getRangeAt(0).cloneRange();
      menu(range, [['Werte', Object.entries(text.labels).map(([key, label]) => [key, label, text.values[key]])]], key => {
        host.focus();
        getSelection().removeAllRanges();
        getSelection().addRange(range);
        if (!key) return;
        insert(range, pill({f: key}, text.values[key], text.labels[key] || key));
        host.dispatchEvent(new Event('input'));
      });
    });
    document.addEventListener('selectionchange', () => {
      const s = getSelection();
      if (s.rangeCount && s.anchorNode && form.contains(s.anchorNode) && s.anchorNode.parentElement?.closest('[data-words]')) saved = s.getRangeAt(0).cloneRange();
    });
    form.addEventListener('click', e => {
      const b = e.target.closest('[data-insert]');
      const host = saved && (saved.startContainer.nodeType === Node.ELEMENT_NODE ? saved.startContainer : saved.startContainer.parentElement).closest('[data-words]');
      if (!b || !host) return;
      const text = data[host.dataset.text], key = b.dataset.insert;
      host.focus();
      insert(saved, pill({f: key}, text.values[key], text.labels[key] || key));
      host.dispatchEvent(new Event('input'));
    });
  }
  document.querySelectorAll('form[data-words-page]').forEach(page);

  return {esc, brs, runs, notation, pill, inner, shown, read, typing, insert, menu};
})();
