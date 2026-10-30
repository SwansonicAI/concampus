// ConCampus in the browser: forgiving list filters, dates in plain words, the search palette, the side panel and
// keyboard shortcuts. Everything works without it; it only removes clicks.

// Matching ignores case, accents and umlaut spellings, like the server does.
const fold = text => text.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase()
  .replace(/ß/g, 'ss').replace(/ae/g, 'a').replace(/oe/g, 'o').replace(/ue/g, 'u');

function init(root) {
  root.querySelectorAll('.form div[id^="id_"]').forEach(list => (list.dataset.search || list.querySelectorAll('input[type=checkbox]').length > 8) && filterable(list));
  root.querySelectorAll('datalist[data-suggest]').forEach(suggest);
  root.querySelectorAll('.form select').forEach(select => (select.options.length > 60 || 'find' in select.dataset) && findable(select));
  root.querySelectorAll('select[data-leads]').forEach(lead);
  root.querySelectorAll('select[data-town]').forEach(placed);
  root.querySelectorAll('form:has([name=worst])').forEach(converted);
  root.querySelectorAll('select[data-describes]').forEach(describe);
  root.querySelectorAll('[data-day], [data-moment], [data-past]').forEach(dated);
  root.querySelectorAll('form:has([data-for])').forEach(conditional);
  root.querySelectorAll('.lines').forEach(lineChart);
  root.querySelectorAll('table:not(.hours)').forEach(sortable);
  root.querySelectorAll('table').forEach(scrolling);
}

// A wide table scrolls within itself, never the page.
function scrolling(table) {
  if (table.closest('.scroll')) return;
  const box = Object.assign(document.createElement('div'), {className: 'scroll'});
  table.before(box);
  box.append(table);
}

const MONTHS = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'];

// Column headings sort the rows: numbers largest first, dates and text in order, a second click reverses, empty cells
// stay last. Headings marked data-sort ask the server instead, for lists too long to show at once. The order lasts the
// session, so it survives filtering and coming back.
function sortable(table) {
  const heads = [...table.tHead?.rows[0].cells ?? []];
  const rows = [...table.tBodies[0]?.rows ?? []];
  const server = heads.some(th => th.dataset.sort);
  if (!server && (rows.length < 2 || rows.some(row => row.cells.length !== heads.length))) return;  // nothing to sort, or grouped
  const saved = table.closest('main') && `sort ${location.pathname} ${[...document.querySelectorAll('main table')].indexOf(table)}`;
  const value = cell => {
    const text = (cell.dataset.value ?? cell.textContent.trim()).replace(/(\d{1,2})\. (\p{L}+) (\d{4})/u, (all, d, m, y) => MONTHS.includes(m) ? `${d}.${MONTHS.indexOf(m) + 1}.${y}` : all);
    const date = text.match(/(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\D+(\d{1,2}):(\d{2}))?/);
    if (date) return `${date[3]}-${date[2].padStart(2, '0')}-${date[1].padStart(2, '0')} ${date[4]?.padStart(2, '0') ?? ''}${date[5] ?? ''}`;
    if (/^-?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?( ?%)?$/.test(text)) return Number(text.replace(/[.%\s]/g, '').replace(',', '.'));
    return text === '–' ? '' : text;
  };
  const order = (col, down) => {
    const values = new Map(rows.map(row => [row, value(row.cells[col])]));
    rows.sort((r, s) => {
      const [a, b] = down ? [values.get(s), values.get(r)] : [values.get(r), values.get(s)];
      if (a === '' || b === '') return (values.get(r) === '') - (values.get(s) === '');
      return typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b), 'de', {numeric: true});
    });
    table.tBodies[0].append(...rows);
    heads.forEach((th, i) => i === col ? th.setAttribute('aria-sort', down ? 'descending' : 'ascending') : th.removeAttribute('aria-sort'));
    if (saved) sessionStorage.setItem(saved, `${col} ${down}`);
  };
  heads.forEach((th, col) => {
    if (!th.textContent.trim()) return;  // e.g. the box that ticks all
    const button = Object.assign(document.createElement('button'), {type: 'button'});
    button.append(...th.childNodes);
    th.append(button);
    button.addEventListener('click', async () => {
      const sorted = th.getAttribute('aria-sort');
      if (!th.dataset.sort) return order(col, sorted ? sorted === 'ascending' : rows.every(row => typeof value(row.cells[col]) !== 'string' || !value(row.cells[col])));
      const url = new URL(location.href);
      url.searchParams.set('sortierung', sorted === 'ascending' ? `-${th.dataset.sort}` : th.dataset.sort);
      await swap(url);
      document.querySelector(`main th[data-sort="${th.dataset.sort}"] button`)?.focus();
    });
  });
  const [col, down] = (!server && saved && sessionStorage.getItem(saved)?.split(' ')) || [];
  if (heads[col]) order(Number(col), down === 'true');
}

// Line charts are drawn here from their data, at the size they are shown: the legend turns lines on and off and the
// scale follows what is left; the pointer reads every line at the nearest point in time and brings the closest forward.
const SVG = 'http://www.w3.org/2000/svg';

// A smooth path through the points that never overshoots them (monotone cubic), so curves invent no peaks.
function curve(points) {
  const n = points.length;
  if (n < 3) return points.map(([x, y], i) => `${i ? 'L' : 'M'}${x},${y}`).join('');
  const d = points.slice(1).map(([x, y], i) => (y - points[i][1]) / (x - points[i][0]));
  const m = points.map((_, i) => i === 0 ? d[0] : i === n - 1 ? d[n - 2] : d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2);
  for (let i = 0; i < n - 1; i++) {
    if (d[i] === 0) { m[i] = m[i + 1] = 0; continue; }
    const a = m[i] / d[i], b = m[i + 1] / d[i], h = a * a + b * b;
    if (h > 9) { m[i] = 3 * a / Math.sqrt(h) * d[i]; m[i + 1] = 3 * b / Math.sqrt(h) * d[i]; }
  }
  return points.map(([x, y], i) => {
    if (!i) return `M${x},${y}`;
    const [px, py] = points[i - 1], third = (x - px) / 3;
    return `C${px + third},${py + m[i - 1] * third},${x - third},${y - m[i] * third},${x},${y}`;
  }).join('');
}

function lineChart(chart) {
  const data = JSON.parse(chart.querySelector('script').textContent);
  const svg = chart.querySelector('svg'), tip = chart.querySelector('.tip'), keys = [...chart.querySelectorAll('.key')];
  const [H, T, B, L, R] = [280, 16, 30, 56, 64];
  const last = data.x.length - 1;
  const fmt = v => v.toLocaleString('de-DE', {maximumFractionDigits: data.decimals}) + data.unit;
  const add = (name, attrs, parent = svg) => {
    const node = document.createElementNS(SVG, name);
    Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
    return parent.appendChild(node);
  };
  const shown = () => data.series.filter((s, i) => !keys.length || keys[i].getAttribute('aria-pressed') === 'true');
  const focus = s => {  // one line forward, the others back; none: all equal
    svg.classList.toggle('focus', Boolean(s));
    svg.querySelectorAll('.line').forEach(path => path.classList.toggle('on', path.dataset.series === String(data.series.indexOf(s))));
  };
  let X, Y, hover;
  const draw = () => {
    const W = svg.getBoundingClientRect().width;
    if (!W) return;
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.replaceChildren();
    const lines = shown();
    const peak = Math.max(1, ...lines.flatMap(s => s.values));
    const step = [1, 2, 2.5, 5, 10].map(f => f * 10 ** Math.floor(Math.log10(peak / 4))).find(v => v * 4 >= peak);
    const top = Math.ceil(peak / step) * step;
    X = i => L + i * (W - L - R) / Math.max(1, last);
    Y = v => T + (H - T - B) * (1 - v / top);
    for (let v = 0; v <= top + step / 2; v += step) {
      add('line', {class: v ? 'grid' : 'base', x1: L, x2: W - R, y1: Y(v), y2: Y(v)});
      add('text', {class: 'ytick', x: L - 10, y: Y(v)}).textContent = fmt(v);
    }
    const every = Math.ceil((last + 1) / Math.max(2, Math.floor((W - L - R) / 72)));
    data.x.forEach((label, i) => (last - i) % every === 0 && (add('text', {class: 'xtick', x: X(i), y: H - 8}).textContent = label));
    const points = s => s.values.map((v, i) => [X(i), Y(v)]);
    if (lines.length === 1) add('path', {class: 'area', fill: lines[0].color, d: `${curve(points(lines[0]))}L${X(last)},${Y(0)}L${X(0)},${Y(0)}Z`});
    for (const s of lines) {
      add('path', {class: 'line', stroke: s.color, d: curve(points(s)), 'data-series': data.series.indexOf(s)});
      add('circle', {class: 'dot', cx: X(last), cy: Y(s.values[last]), r: 4, fill: s.color});
    }
    let above = -Infinity;  // the value at each line's end, where it does not crowd the one above
    [...lines].sort((a, b) => b.values[last] - a.values[last]).forEach(s => {
      const y = Y(s.values[last]);
      if (y - above < 15) return;
      above = y;
      add('text', {class: 'end', x: X(last) + 10, y}).textContent = fmt(s.values[last]);
    });
    hover = add('g', {visibility: 'hidden'});
    add('line', {class: 'cross', y1: T, y2: H - B}, hover);
    lines.forEach(s => add('circle', {class: 'dot', r: 4, fill: s.color}, hover));
  };
  keys.forEach((key, i) => {
    key.addEventListener('click', () => {
      key.setAttribute('aria-pressed', key.getAttribute('aria-pressed') !== 'true');
      draw();
    });
    key.addEventListener('pointerenter', () => key.getAttribute('aria-pressed') === 'true' && focus(data.series[i]));
    key.addEventListener('pointerleave', () => focus(null));
  });
  svg.addEventListener('pointermove', event => {
    const box = svg.getBoundingClientRect();
    const x = event.clientX - box.left, y = event.clientY - box.top;
    const i = Math.max(0, Math.min(last, Math.round((x - L) / (box.width - L - R) * last)));
    const lines = shown();
    const near = lines.reduce((best, s) => Math.abs(Y(s.values[i]) - y) < Math.abs(Y(best.values[i]) - y) ? s : best, lines[0]);
    focus(lines.length > 1 && near);
    const [cross, ...dots] = hover.children;
    cross.setAttribute('x1', X(i));
    cross.setAttribute('x2', X(i));
    dots.forEach((dot, k) => { dot.setAttribute('cx', X(i)); dot.setAttribute('cy', Y(lines[k].values[i])); });
    hover.setAttribute('visibility', 'visible');
    const rows = [...lines].sort((a, b) => b.values[i] - a.values[i]).map(s => {
      const row = document.createElement('div');
      row.style.setProperty('--c', s.color);
      row.classList.toggle('on', lines.length > 1 && s === near);
      row.append(Object.assign(document.createElement('b'), {textContent: fmt(s.values[i])}), ' ', s.label);
      return row;
    });
    tip.replaceChildren(Object.assign(document.createElement('small'), {textContent: data.full[i]}), ...rows);
    tip.style.left = `${X(i)}px`;
    tip.style.transform = X(i) > box.width * 0.6 ? 'translateX(calc(-100% - 14px))' : 'translateX(14px)';
    tip.hidden = false;
  });
  svg.addEventListener('pointerleave', () => {
    tip.hidden = true;
    hover?.setAttribute('visibility', 'hidden');
    focus(null);
  });
  new ResizeObserver(draw).observe(svg);
}

// People found by name from three letters, a few at a time: who can be added is found, never listed in full.
const people = async (url, q) => q.length < 3 ? [] : (await (await fetch(`${url}?q=${encodeURIComponent(q)}`)).json()).found;

function suggest(list) {
  const input = document.querySelector(`[list="${list.id}"]`);
  let timer;
  input?.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const found = await people(list.dataset.suggest, input.value.trim());
      list.replaceChildren(...found.map(p => Object.assign(document.createElement('option'), {value: p.value, textContent: p.text})));
    }, 150);
  });
}

// Type-to-filter for long choice lists; a list of people (data-search) adds whoever else the name finds.
function filterable(list) {
  const input = Object.assign(document.createElement('input'), {type: 'search', className: 'filter', placeholder: list.dataset.search ? 'Name tippen …' : 'Liste filtern …'});
  list.before(input);
  let timer;
  list.dataset.search && input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      for (const p of await people(list.dataset.search, input.value.trim())) {
        if (list.querySelector(`input[value="${p.id}"]`)) continue;
        const row = document.createElement('div'), label = document.createElement('label');
        label.append(Object.assign(document.createElement('input'), {type: 'checkbox', name: list.dataset.name, value: p.id}), p.text);
        row.append(label);
        list.append(row);
      }
      input.dispatchEvent(new Event('filter'));
    }, 150);
  });
  const narrow = () => {
    const words = fold(input.value).split(/\s+/).filter(Boolean);
    for (const label of list.querySelectorAll('label:has(input)')) {
      const box = label.querySelector('input');
      label.parentElement.hidden = !box.checked && !words.every(word => fold(label.textContent).includes(word));
    }
    for (const group of list.children) {  // programme headings disappear with all their entries
      if (group.querySelector(':scope > label:not(:has(input))')) {
        group.hidden = !group.querySelector(':scope > div:not([hidden])');
      }
    }
  };
  input.addEventListener('input', narrow);
  input.addEventListener('filter', narrow);

  // What is ticked shows above, with how many and, in a grouped list, its group: a long list hides nothing. A chip unticks.
  const chosen = Object.assign(document.createElement('div'), {className: 'chips'});
  input.before(chosen);
  const sum = () => {
    const ticked = [...list.querySelectorAll('input[type=checkbox]:checked')];
    chosen.replaceChildren(...(ticked.length ? [Object.assign(document.createElement('small'), {className: 'muted', textContent: `${ticked.length} ausgewählt`})] : []), ...ticked.map(box => {
      const group = box.closest(`#${list.id} > div`)?.querySelector(':scope > label:not(:has(input))')?.textContent.trim();
      const chip = Object.assign(document.createElement('button'), {type: 'button', className: 'chip yes', title: 'Abwählen'});
      chip.textContent = `${box.parentElement.textContent.trim()}${group ? ` · ${group}` : ''} ×`;
      chip.addEventListener('click', () => { box.checked = false; box.dispatchEvent(new Event('change', {bubbles: true})); });
      return chip;
    }));
  };
  list.addEventListener('change', sum);
  sum();
}

// Type-to-find in a long dropdown, such as every Kreis and state: only entries matching the words, or their group, stay.
function findable(select) {
  const input = Object.assign(document.createElement('input'), {type: 'search', className: 'filter', placeholder: 'Liste filtern …'});
  select.before(input);
  const children = [...select.children].map(child => [child, [...child.children]]);
  input.addEventListener('input', () => {
    const kept = select.value;  // taking options out and back loses the choice
    const words = fold(input.value).split(/\s+/).filter(Boolean);
    const within = select.dataset.within;  // the group a leading choice keeps it to (lead), or '!' and the one it keeps out (placed)
    const kept_in = label => !within || (within.startsWith('!') ? label !== within.slice(1) : label === within);
    const fits = (option, group = '') => option.value === kept || !option.value || words.every(word => fold(`${option.text} ${group}`).includes(word));
    select.replaceChildren(...children.flatMap(([child, options]) => {
      if (child.tagName !== 'OPTGROUP') return fits(child) ? [child] : [];
      child.replaceChildren(...options.filter(option => (kept_in(child.label) || option.value === kept) && fits(option, child.label)));
      return child.children.length ? [child] : [];
    }));
    select.value = kept;
  });
}

// A choice that narrows the lists it leads (data-leads="audition teacher") to its own group: the Studiengänge and
// teachers of the Hauptfach picked, the modules of the Studiengang. Ticked entries stay, so saving loses nothing. A list
// with data-widen offers the rest for the exception; one with a find box narrows within the group as one types.
const heading = child => child.tagName === 'OPTGROUP' ? child.label : child.querySelector(':scope > label:not(:has(input))')?.textContent.trim();

function lead(select) {
  for (const name of select.dataset.leads.split(' ')) {
    const list = select.form.querySelector(`#id_${name}`);
    const children = [...list.children];
    const find = list.previousElementSibling?.matches('input.filter') && list.previousElementSibling;
    const widen = list.dataset.widen && Object.assign(document.createElement('button'), {type: 'button', className: 'link', textContent: list.dataset.widen});
    if (widen) list.after(widen);
    const narrow = (all = false) => {
      const option = select.selectedOptions[0];
      const group = !all && option?.value && (option.dataset.group ?? option.text);
      if (widen) widen.hidden = !group;
      if (find) {
        if (group && list.selectedOptions[0]?.parentElement.label !== group) list.value = '';
        list.dataset.within = group || '';
        find.dispatchEvent(new Event('input'));
        return;
      }
      const kept = list.value;
      list.replaceChildren(...children.filter(child => {
        const label = heading(child);
        return !group || label === undefined || label === group || child.querySelector('input:checked');
      }));
      if (list.tagName === 'SELECT') {  // a dropdown keeps its choice where it still can, else none
        list.value = kept;
        if (list.selectedIndex < 0) list.value = '';
      }
    };
    widen?.addEventListener('click', () => narrow(true));
    select.addEventListener('change', () => narrow());
    narrow();
  }
}

// Where someone lives or gained the HZB (data-town names the town's field): first Germany or abroad; in Germany by town
// or postcode, which sets the Kreis and keeps the town; abroad the state, typed to find. The Kreise stay to choose from,
// for a place the search does not know.
function placed(select) {
  const town = select.form.elements[select.dataset.town];
  const find = select.previousElementSibling?.matches('input.filter') ? select.previousElementSibling : null;
  const where = document.createElement('select');
  where.append(...[['', 'In Deutschland oder im Ausland?'], ['de', 'In Deutschland'], ['abroad', 'Im Ausland']].map(([value, text]) => new Option(text, value)));
  const search = Object.assign(document.createElement('input'), {type: 'search', className: 'filter', placeholder: 'Ort oder Postleitzahl, z. B. Apolda'});
  where.setAttribute('aria-label', 'In Deutschland oder im Ausland');
  search.setAttribute('aria-label', 'Ort oder Postleitzahl');
  const found = Object.assign(document.createElement('datalist'), {id: `${select.id}-orte`});
  search.setAttribute('list', found.id);
  (find || select).before(where, search, found);
  const show = () => {
    search.hidden = where.value !== 'de';
    if (find) find.hidden = where.value === 'de';
    select.dataset.within = where.value === 'abroad' ? 'Ausland' : where.value === 'de' ? '!Ausland' : '';
    find?.dispatchEvent(new Event('input'));
  };
  where.value = select.value ? (select.value.startsWith('99') ? 'abroad' : 'de') : '';
  search.value = town?.dataset.name || '';
  where.addEventListener('change', () => {
    select.value = search.value = '';
    if (town) town.value = '';
    show();
  });
  let timer;
  search.addEventListener('input', () => {
    const picked = [...found.options].find(option => option.value === search.value);
    if (picked) {  // a town chosen: its Kreis, and the town kept
      select.value = picked.dataset.kreis;
      if (town) town.value = picked.dataset.code;
      return;
    }
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = search.value.trim();
      const hits = q.length > 1 ? await (await fetch(`/orte/?q=${encodeURIComponent(q)}`)).json() : [];
      found.replaceChildren(...hits.map(hit => {
        const option = new Option('', hit.text);
        Object.assign(option.dataset, {kreis: hit.kreis, code: hit.code});
        return option;
      }));
    }, 150);
  });
  select.addEventListener('change', () => { if (town) town.value = ''; });  // chosen from the list: no town to tell
  show();
}

// A choice that brings a note to another field (data-describes="repertoire"): what the audition chosen asks for.
function describe(select) {
  const hint = Object.assign(document.createElement('small'), {className: 'muted'});
  select.form.elements[select.dataset.describes].after(hint);
  const show = () => { hint.textContent = select.selectedOptions[0]?.dataset.note ?? ''; };
  select.addEventListener('change', show);
  show();
}

// Anerkennung: the grade the modified Bavarian formula makes of a foreign one, shown while it is typed, cut to one
// decimal as the server does.
function converted(form) {
  const inputs = ['foreign', 'best', 'worst'].map(name => form.elements[name]);
  const hint = Object.assign(document.createElement('small'), {className: 'muted'});
  inputs[2].after(hint);
  form.addEventListener('input', () => {
    const [foreign, best, worst] = inputs.map(input => parseFloat(input.value.replace(',', '.')));
    const grade = Math.min(4, Math.max(1, Math.floor((1 + 3 * (best - foreign) / (best - worst)) * 10 + 1e-9) / 10));
    hint.textContent = [foreign, best, worst].some(Number.isNaN) || best === worst ? '' : `ergibt ${grade.toFixed(1).replace('.', ',')}`;
  });
}

// A time in an applicant's programme (data-seek) plays the video from there.
document.addEventListener('click', event => {
  const mark = event.target.closest('[data-seek]');
  const video = mark && document.querySelector('.review video');
  if (!video) return;
  video.currentTime = Number(mark.dataset.seek);
  video.play();
});

// What cannot be undone (data-confirm="Wirklich auflösen?") asks once more: the first click turns the button into the
// question, no narrower than before, a second does it. A click elsewhere or Escape takes the question back.
function disarm(button) {
  button.innerHTML = button.dataset.label;
  button.style.minWidth = '';
  delete button.dataset.armed;
}
document.addEventListener('click', event => {
  const button = event.target.closest('button[data-confirm]');
  document.querySelectorAll('button[data-armed]').forEach(armed => armed === button || disarm(armed));
  if (!button || button.dataset.armed) return;
  event.preventDefault();
  button.style.minWidth = `${button.offsetWidth}px`;
  button.dataset.label = button.innerHTML;
  button.dataset.armed = '1';
  button.textContent = button.dataset.confirm;
});
document.addEventListener('keydown', event => event.key === 'Escape' && document.querySelectorAll('button[data-armed]').forEach(disarm));

// A box that ticks a group (data-all="2") ticks every box of that group (data-part="2"); without a value, every box.
document.addEventListener('change', ({target}) => {
  if (!target.matches('[data-all]')) return;
  target.form.querySelectorAll(target.dataset.all ? `[data-part="${target.dataset.all}"]` : '[data-part], [data-all]').forEach(box => { box.checked = target.checked; });
});

// Fields that matter only for some choices of another (data-for="category:kammermusik") show only then.
function conditional(form) {
  const rules = new Map();
  form.querySelectorAll('[data-for]').forEach(input => rules.set(input.closest('.row > div, form > div'), input.dataset.for));
  for (const [group, rule] of rules) {
    const [name, values] = rule.split(':');
    const choice = form.elements[name];
    const show = () => { group.hidden = !values.split(',').includes(choice.value); };
    choice.addEventListener('change', show);
    show();
  }
}

// Date fields take 'morgen', 'fr', '12.11.' and say what they understood; arrow keys move a day, with Shift a week.
const WEEKDAYS = ['So', 'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa'];

function dated(input) {
  const hint = Object.assign(document.createElement('small'), {className: 'hint'});
  const box = Object.assign(document.createElement('span'), {className: 'when'});
  input.replaceWith(box);
  box.append(input, hint);
  let timer;
  const understand = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      const only = 'day' in input.dataset ? '&nur=tag' : 'past' in input.dataset ? '&nur=vergangen' : '';
      const found = q ? await (await fetch(`/datum/?q=${encodeURIComponent(q)}${only}`)).json() : {};
      input.dataset.iso = found.iso || '';
      hint.textContent = q ? found.text || found.error || 'Nicht erkannt' : '';
      hint.classList.toggle('bad', Boolean(q && !found.text));
    }, 120);
  };
  if ('past' in input.dataset) {  // a day that has been: digits and their separators only
    input.addEventListener('input', () => { input.value = input.value.replace(/[^\d./-]/g, ''); });
  }
  input.addEventListener('input', understand);
  input.addEventListener('focus', () => { input.select(); understand(); });
  if (!('day' in input.dataset)) return;
  input.addEventListener('keydown', event => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    const shown = input.value.match(/(\d{1,2})\.(\d{1,2})\.(\d{4})/);
    const iso = input.dataset.iso || (shown && `${shown[3]}-${shown[2].padStart(2, '0')}-${shown[1].padStart(2, '0')}`);
    if (!iso) return;
    event.preventDefault();
    const day = new Date(`${iso}T12:00`);
    day.setDate(day.getDate() + (event.key === 'ArrowUp' ? 1 : -1) * (event.shiftKey ? 7 : 1));
    const two = n => String(n).padStart(2, '0');
    input.value = `${WEEKDAYS[day.getDay()]} ${two(day.getDate())}.${two(day.getMonth() + 1)}.${day.getFullYear()}`;
    understand();
  });
}

// Search palette: results while typing, arrow keys to choose, Enter to go.
const search = document.querySelector('.search');
if (search) {
  const input = search.querySelector('input');
  const list = Object.assign(document.createElement('div'), {className: 'palette'});
  search.append(list);
  let timer, shown = null;
  const load = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      if (q !== shown) {
        if (list.querySelector('.empty')) search.classList.remove('open');  // not the last query's „Nichts gefunden“ meanwhile
        const response = await fetch(`${search.action}?q=${encodeURIComponent(q)}`, {headers: {'X-Fragment': '1'}});
        if (q !== input.value.trim()) return;  // typing went on
        list.innerHTML = await response.text();
        list.querySelector('a')?.classList.add('on');
        shown = q;
      }
      search.classList.toggle('open', Boolean(list.textContent.trim()));
    }, 90);
  };
  input.addEventListener('focus', load);
  input.addEventListener('input', load);
  input.addEventListener('keydown', event => {
    const links = [...list.querySelectorAll('a')];
    const at = links.findIndex(a => a.classList.contains('on'));
    if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && links.length) {
      event.preventDefault();
      const next = links[(at + (event.key === 'ArrowDown' ? 1 : -1) + links.length) % links.length];
      links.forEach(a => a.classList.toggle('on', a === next));
      next.scrollIntoView({block: 'nearest'});
    } else if (event.key === 'Enter' && at >= 0 && search.classList.contains('open') && shown === input.value.trim()) {
      event.preventDefault();
      location.href = links[at].href;
    } else if (event.key === 'Escape') {
      search.classList.remove('open');
      input.blur();
    }
  });
  document.addEventListener('click', event => search.contains(event.target) || search.classList.remove('open'));
}

// Filters apply in place: a pause in typing or a new choice updates the page without reloading it, cursor included.
const inPlace = form => form?.method === 'get' && form.closest('main');
let pause;
document.addEventListener('input', event => {
  if (event.target.type === 'search' && inPlace(event.target.form)) {
    clearTimeout(pause);
    pause = setTimeout(() => event.target.form.requestSubmit(), 300);
  }
});
document.addEventListener('submit', event => {
  const form = event.target;
  if (!inPlace(form) || event.submitter?.hasAttribute('formaction')) return;  // a button with its own target leaves the page
  event.preventDefault();
  const url = new URL(form.action);
  url.search = new URLSearchParams(new FormData(form));
  swap(url);
});

async function swap(url) {
  const field = document.activeElement, typed = field.value;
  document.querySelector('main').setAttribute('aria-busy', 'true');  // the new page replaces it, and the mark with it
  const html = await (await fetch(url)).text();
  if (field.value !== typed) return;  // typing went on; its own request follows
  const main = new DOMParser().parseFromString(html, 'text/html').querySelector('main');
  document.querySelector('main').replaceWith(main);
  history.replaceState(history.state, '', url);
  init(main);
  const again = field.name && main.querySelector(`[name="${field.name}"]`);
  again?.focus();
  if (again?.type === 'search') again.setSelectionRange(typed.length, typed.length);
}

// A file over its limit is caught before a long upload starts.
document.addEventListener('change', event => {
  const input = event.target;
  if (input.type === 'file' && input.dataset.max) {
    input.setCustomValidity([...input.files].some(f => f.size > Number(input.dataset.max)) ? input.dataset.tooBig : '');
    input.reportValidity();
  }
});

// The browser says what is missing or wrong in its own language: here in German, as the page is.
function wording(field) {
  const v = field.validity;
  if (v.valueMissing) return {radio: 'Bitte eine der Möglichkeiten wählen.', checkbox: 'Bitte bestätigen.', file: 'Bitte eine Datei wählen.', 'select-one': 'Bitte auswählen.'}[field.type] || 'Bitte ausfüllen.';
  if (v.typeMismatch) return field.type === 'email' ? 'Bitte eine E-Mail-Adresse angeben.' : 'Bitte eine Webadresse angeben.';
  if (v.rangeUnderflow) return `Mindestens ${field.min}.`;
  if (v.rangeOverflow) return `Höchstens ${field.max}.`;
  return '';
}
document.addEventListener('invalid', ({target}) => target.validity.customError || target.setCustomValidity(wording(target)), true);
document.addEventListener('input', ({target}) => {
  if (!target.willValidate || target.dataset.max) return;  // a file's size check keeps its own word
  const group = target.type === 'radio' && target.form ? target.form.querySelectorAll(`[name="${CSS.escape(target.name)}"]`) : [target];
  group.forEach(field => field.setCustomValidity(''));
});

// Uploads show their progress, since videos are large, in the side panel too; then the page goes where the form
// leads. A failed or broken upload says so and can be tried again.
document.addEventListener('submit', event => {
  const form = event.target;
  if (!form.matches('[data-upload]') || !form.checkValidity()) return;
  event.preventDefault();
  const button = form.querySelector('button');
  const label = button.textContent;
  const request = new XMLHttpRequest();
  const failed = () => {
    button.disabled = false;
    button.textContent = label;
    const note = form.querySelector('.upload-failed') || form.appendChild(Object.assign(document.createElement('small'), {className: 'fail upload-failed'}));
    note.textContent = 'Hochladen fehlgeschlagen. Bitte die Verbindung prüfen und erneut hochladen.';
  };
  request.upload.addEventListener('progress', e => { button.textContent = `${Math.floor(e.loaded / e.total * 100)} % hochgeladen`; });
  request.addEventListener('load', () => request.status < 400 ? location.assign(request.getResponseHeader('X-Location') || location.href) : failed());
  request.addEventListener('error', failed);
  request.addEventListener('abort', failed);
  request.open('POST', form.action);
  request.setRequestHeader('X-Panel', '1');  // answered with where it leads instead of following, so the message is still there
  request.send(new FormData(form));
  button.disabled = true;
});

// A form typed into and not saved is not left without asking: leaving the page, the browser asks; „Abbrechen“ or
// closing the panel asks here first.
const dirty = root => [...root.querySelectorAll('form[method=post]')].some(form => form.dataset.dirty);
const leave = () => window.confirm('Ungespeicherte Eingaben verwerfen?');
document.addEventListener('input', ({target}) => { if (target.form?.method === 'post') target.form.dataset.dirty = '1'; });
document.addEventListener('submit', ({target}) => { delete target.dataset.dirty; });
addEventListener('beforeunload', event => { if (dirty(document)) event.preventDefault(); });
document.addEventListener('click', event => {
  const cancel = event.target.closest('a.cancel, a[data-close]');
  if (cancel && !cancel.closest('.drawer') && dirty(document) && !leave()) event.preventDefault();
  else if (cancel && !cancel.closest('.drawer')) document.querySelectorAll('form[data-dirty]').forEach(form => delete form.dataset.dirty);
});

// The account menu closes on Escape or a click elsewhere, and gives way to the search's results.
const menu = document.querySelector('details.menu');
if (menu) {
  document.addEventListener('click', event => menu.open && !menu.contains(event.target) && menu.removeAttribute('open'));
  document.addEventListener('keydown', event => event.key === 'Escape' && menu.open && menu.removeAttribute('open'));
  menu.addEventListener('toggle', () => menu.open && document.querySelector('.search')?.classList.remove('open'));
  document.querySelector('.search input')?.addEventListener('focus', () => menu.removeAttribute('open'));
}

// Side panel: details open beside the page instead of replacing it. Browser back or Esc closes it. A form saved in
// it returns to the panel's page or closes the panel; either way the page underneath reloads once it closes.
const drawer = document.querySelector('.drawer');
if (drawer) {
  const body = drawer.querySelector('.drawer-body');
  const page = drawer.querySelector('.page');
  const wider = drawer.querySelector('.wider');
  let stale = false;  // something was saved: the page underneath reloads once the panel closes

  const show = async (url, options = {}) => {
    const response = await fetch(url, {...options, headers: {'X-Panel': '1'}});
    stale ||= response.headers.has('X-Stale');  // e.g. a message read: the Postfach's count
    if (response.status === 204) {
      stale = true;
      const next = new URL(response.headers.get('X-Location'), location.href);
      return next.pathname === new URL(page.href).pathname ? show(next) : history.back();
    }
    const html = response.headers.get('Content-Type')?.startsWith('text/html') && await response.text();
    if (!html || /^\s*<!doctype/i.test(html)) return location.assign(response.url);  // files, and pages of their own
    body.innerHTML = html;
    page.href = response.url;
    if (body.querySelector('[data-wide]')) {  // a results sheet needs every column
      drawer.classList.add('wide');
      wider.textContent = 'Schmaler';
    }
    if (!drawer.classList.contains('open')) {
      history.pushState({drawer: true}, '');
      drawer.classList.add('open');
    }
    drawer.scrollTop = 0;
    const first = body.querySelector('h1, h2') || body;  // a screen reader starts where the panel does
    first.tabIndex = -1;
    first.focus({preventScroll: true});
    init(body);
  };
  let opener = null;  // where focus goes back to once the panel closes
  const close = () => drawer.classList.contains('open') && (!dirty(body) || leave()) && history.back();

  addEventListener('popstate', () => {
    if (stale) return location.reload();
    drawer.classList.remove('open', 'wide');
    wider.textContent = 'Breiter';
    body.innerHTML = '';
    opener?.focus();
    opener = null;
  });
  drawer.querySelector('.close').addEventListener('click', close);
  wider.addEventListener('click', () => {
    wider.textContent = drawer.classList.toggle('wide') ? 'Schmaler' : 'Breiter';
  });

  // Dragging the green edge sets the panel's width, which it keeps from then on.
  const edge = event => event.clientX - drawer.getBoundingClientRect().left < 12;
  drawer.style.setProperty('--width', localStorage.getItem('panel width'));
  drawer.addEventListener('pointerdown', event => {
    if (event.button || !edge(event)) return;
    event.preventDefault();
    drawer.setPointerCapture(event.pointerId);
  });
  drawer.addEventListener('pointermove', event => {
    if (!drawer.hasPointerCapture(event.pointerId)) return drawer.classList.toggle('edge', edge(event));
    drawer.classList.remove('wide');
    wider.textContent = 'Breiter';
    drawer.style.setProperty('--width', `${Math.min(innerWidth - 48, Math.max(420, innerWidth - event.clientX))}px`);
  });
  drawer.addEventListener('pointerleave', () => drawer.classList.remove('edge'));
  drawer.addEventListener('lostpointercapture', () => localStorage.setItem('panel width', drawer.style.getPropertyValue('--width')));

  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || link === page || link.target || link.hasAttribute('download') || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const inside = drawer.contains(link);
    if (!inside && !link.closest('[data-panel]')) return;
    const url = new URL(link.getAttribute('href'), inside ? page.href : location.href);
    if (url.origin !== location.origin) return;
    event.preventDefault();
    if (inside && (link.hasAttribute('data-close') || url.pathname === location.pathname)) return close();  // back to the page underneath, e.g. 'Abbrechen'
    if (!inside) opener = link;
    show(url);
  });
  document.addEventListener('submit', event => {
    const form = event.target;
    if (!drawer.contains(form) || form.matches('[data-upload]')) return;  // uploads go their own way, with progress
    event.preventDefault();
    const url = new URL(form.getAttribute('action') || page.href, page.href);
    const data = new FormData(form, event.submitter);
    if (form.method === 'get') {
      url.search = new URLSearchParams(data);
      show(url);
    } else {
      show(url, {method: 'post', body: data});
    }
  });

  // Room plan: dragging across free time books exactly that span; a click books an hour.
  let pick = null;
  document.addEventListener('pointerdown', event => {
    const slot = event.target.closest('.slot');
    if (!slot || event.pointerType !== 'mouse' || event.button) return;
    event.preventDefault();  // neither drag the link nor select text
    pick = slot;
  });
  document.addEventListener('pointerover', event => {
    const slot = event.target.closest('.slot');
    if (!pick || slot?.parentElement !== pick.parentElement) return;
    const slots = [...slot.parentElement.querySelectorAll('.slot')];
    const [from, to] = [slots.indexOf(pick), slots.indexOf(slot)].sort((a, b) => a - b);
    slots.forEach((s, n) => s.classList.toggle('picked', n >= from && n <= to));
  });
  document.addEventListener('pointerup', () => {
    if (!pick) return;
    const picked = [...pick.parentElement.querySelectorAll('.picked')];
    picked.forEach(s => s.classList.remove('picked'));
    pick = null;
    if (picked.length < 2) return;
    const url = new URL(picked[0].href);
    const [day, time] = new URL(picked.at(-1).href).searchParams.get('start').split('T');
    const [hours, minutes] = time.split(':').map(Number);
    const end = hours * 60 + minutes + 30;
    url.searchParams.set('ende', `${day}T${String(Math.floor(end / 60)).padStart(2, '0')}:${String(end % 60).padStart(2, '0')}`);
    show(url);
  });

  // Keys: Ctrl+K or / searches, Esc closes the panel, and links marked data-key answer to their key.
  document.addEventListener('keydown', event => {
    const typing = event.target.closest('input, select, textarea');
    if ((event.key === 'k' && (event.ctrlKey || event.metaKey)) || (event.key === '/' && !typing)) {
      event.preventDefault();
      search?.querySelector('input').focus();
    } else if (event.key === 'Escape' && !search?.contains(event.target)) {
      close();
    } else if (!typing && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const scope = drawer.classList.contains('open') ? body : document.querySelector('main');
      scope.querySelector(`[data-key="${event.key}"]`)?.click();
    }
  });
}

init(document);
