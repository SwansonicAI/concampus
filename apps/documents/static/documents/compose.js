// A document's content, block by block (documents.compose): each block a card to word, move or remove, a new one added at
// the end. What is typed goes into the form's „inhalt“ as JSON, which app.js (worded) sends to the preview; each card is
// marked (data-mark), so the preview tints it while it is worded, and shows beneath it the placeholders the document knows.
(() => {
  const root = document.querySelector('[data-composer]');
  if (!root) return;
  const data = JSON.parse(document.getElementById(root.dataset.composer).textContent);
  const form = root.closest('form');
  let content = structuredClone(data.composition), store;
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const short = label => label.split(/[,(]/)[0].trim();  // „Zeitraum des Semesters, bei …“: what the button says
  const GAPS = [[0, 'kein'], [1, 'klein'], [2, 'mittel'], [3, 'groß']];
  const ALIGN = [['left', 'links'], ['right', 'rechts']];
  const lists = Object.keys(data.lists), checkable = data.fields.some(([key]) => key === 'pruefadresse');
  const FRESH = {
    title: () => ({text: 'Titel'}), text: () => ({text: '', gap: 1}), facts: () => ({rows: [['', '']], gap: 1}),
    table: () => ({list: lists[0], columns: [['', '', 'left']], gap: 1}), sign: () => ({text: '{ort}, {datum}', by: '{amt}', gap: 3}),
    signatures: () => ({text: 'Ort, Datum\nUnterschrift', gap: 3}), note: () => ({text: '', gap: 2}), qr: () => ({text: 'Zu prüfen unter {pruefadresse}', gap: 3}),
  };
  const offered = Object.keys(data.blocks).filter(kind => (kind !== 'table' || lists.length) && (kind !== 'qr' || checkable));

  const words = (path, value, label) => `<input type="text" data-words data-path="${path}" value="${esc(value)}" placeholder="${esc(label)}" aria-label="${esc(label)}">`;
  const area = (path, value, rows, label) => `<textarea data-path="${path}" rows="${rows}" aria-label="${esc(label)}">${esc(value)}</textarea>`;
  const choose = (path, pairs, value) => `<select data-path="${path}">${pairs.map(([v, l]) => `<option value="${esc(v)}"${String(v) === String(value) ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select>`;
  const act = (name, label, title, i = '') => `<button type="button" class="compose-act" data-act="${name}" data-i="${i}" title="${title}" aria-label="${title}">${label}</button>`;
  function chips(b) {
    const own = b.type === 'table' ? data.lists[b.list]?.[1] || [] : [];
    return `<div class="chips" aria-label="Platzhalter einfügen">${[...own, ...data.fields].map(([key, label, example]) =>
      `<button type="button" class="chip" data-insert="{${key}}" title="${esc(`${label}: ${example || 'leer'}`)}">${esc(short(label))}</button>`).join('')}</div>`;
  }
  function body(b) {
    const [label] = data.blocks[b.type];
    if (b.type === 'facts') {
      return (b.rows || []).map(([l, v], i) => `<div class="compose-row">${words(`rows.${i}.0`, l, 'Bezeichnung')}${words(`rows.${i}.1`, v, 'Wert')}${act('row-remove', '×', 'Zeile entfernen', i)}</div>`).join('') +
        `<button type="button" class="link" data-act="row-add">Zeile hinzufügen</button>`;
    }
    if (b.type === 'table') {
      return `<label class="compose-list">Liste ${choose('list', lists.map(name => [name, data.lists[name][0]]), b.list)}</label>` +
        (b.columns || []).map(([h, cell, align], i) => `<div class="compose-row table">${words(`columns.${i}.0`, h, 'Spalte')}${words(`columns.${i}.1`, cell, 'Inhalt')}` +
          `${choose(`columns.${i}.2`, ALIGN, align)}${act('column-remove', '×', 'Spalte entfernen', i)}</div>`).join('') +
        `<button type="button" class="link" data-act="column-add">Spalte hinzufügen</button>`;
    }
    if (b.type === 'sign') return `<div class="compose-row">${words('text', b.text, 'Links: Ort und Datum')}${words('by', b.by, 'Rechts: wer ausstellt')}</div>`;
    return area('text', b.text, {title: 1, signatures: 3, text: 3}[b.type] || 2, label);
  }
  function card(b, n) {
    const [label, about] = data.blocks[b.type];
    const acts = (n ? act('up', '↑', 'Nach oben') : '') + (n < content.blocks.length - 1 ? act('down', '↓', 'Nach unten') : '') + act('remove', '×', 'Block entfernen');
    return `<li class="compose-block worded" data-mark="block:${n}" data-n="${n}">` +
      `<div class="compose-head"><b title="${esc(about)}">${esc(label)}</b><span>${acts}</span></div>` +
      `<div class="worded-fields">${body(b)}${chips(b)}` +
      `<div class="compose-more"><label>Abstand davor ${choose('gap', GAPS, b.gap || 0)}</label>` +
      `<label>Zeigen ${choose('when', [['', 'immer'], ...data.fields.map(([key, l]) => [key, `nur mit ${short(l)}`])], b.when || '')}</label></div></div></li>`;
  }
  function render() {
    root.innerHTML = `<input type="hidden" name="inhalt">` +
      `<div class="compose-office"><label for="compose-office">Absender</label><input type="text" id="compose-office" data-words data-path="office" value="${esc(content.office)}">` +
      `<small class="muted">Oben im Briefkopf, und wo {amt} steht.</small></div>` +
      `<ol class="compose-blocks">${content.blocks.map(card).join('')}</ol>` +
      `<div class="compose-add"><span class="muted">Block hinzufügen</span>${offered.map(kind => `<button type="button" class="btn ghost small" data-add="${kind}" title="${esc(data.blocks[kind][1])}">${esc(data.blocks[kind][0])}</button>`).join('')}</div>` +
      `<p class="muted compose-hint"><small>{Platzhalter} setzt ConCampus ein, aus den Daten der Person. Was in [eckigen Klammern] steht, entfällt, wenn seine Platzhalter leer sind; eine Zeile ohne Werte ebenso. **fett**</small></p>`;
    store = root.querySelector('[name=inhalt]');
    keep();
  }
  function keep() { store.value = JSON.stringify(content); }
  function changed(again = true) {  // what is kept, the page redrawn where its shape changed, the preview following
    keep();
    if (again) render();
    form.dataset.dirty = '1';
    form.dispatchEvent(new Event('input', {bubbles: true}));
  }
  function set(b, path, value) {
    const keys = path.split('.'), last = keys.pop();
    keys.reduce((at, key) => at[key], b)[last] = last === 'gap' ? +value : value;
  }

  root.addEventListener('input', ({target}) => {
    const path = target.dataset.path, n = target.closest('[data-n]')?.dataset.n;
    if (!path) return;
    if (path === 'office') content.office = target.value;
    else set(content.blocks[n], path, target.value);
    if (path === 'when' && !target.value) delete content.blocks[n].when;
    keep();
    if (path === 'list') changed();  // its columns' placeholders with it
  });
  root.addEventListener('click', ({target}) => {
    const button = target.closest('[data-act], [data-add]');
    if (!button) return;
    if (button.dataset.add) {
      content.blocks.push({type: button.dataset.add, text: '', gap: 0, ...FRESH[button.dataset.add]()});
      changed();
      return root.querySelector('.compose-block:last-of-type :is(textarea, input)')?.focus();
    }
    const n = +button.closest('[data-n]').dataset.n, b = content.blocks[n], i = +button.dataset.i;
    ({
      up: () => content.blocks.splice(n - 1, 0, ...content.blocks.splice(n, 1)),
      down: () => content.blocks.splice(n + 1, 0, ...content.blocks.splice(n, 1)),
      remove: () => content.blocks.splice(n, 1),
      'row-add': () => b.rows.push(['', '']), 'row-remove': () => b.rows.splice(i, 1),
      'column-add': () => b.columns.push(['', '', 'left']), 'column-remove': () => b.columns.splice(i, 1),
    })[button.dataset.act]();
    changed();
  });
  form.querySelector('[data-compose-empty]')?.addEventListener('click', event => {
    if (event.defaultPrevented) return;  // the first click asks (app.js, data-confirm)
    content = {office: '', blocks: []};
    changed();
  });
  render();
})();
