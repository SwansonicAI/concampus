// Campus in the browser: forgiving list filters, dates in plain words, the search palette, the side panel and
// keyboard shortcuts. Everything works without it; it only removes clicks.

// Matching ignores case, accents and umlaut spellings, like the server does.
const fold = text => text.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase()
  .replace(/ß/g, 'ss').replace(/ae/g, 'a').replace(/oe/g, 'o').replace(/ue/g, 'u');

function init(root) {
  root.querySelectorAll('.form div[id^="id_"]').forEach(list => list.querySelectorAll('input[type=checkbox]').length > 8 && filterable(list));
  root.querySelectorAll('[data-day], [data-moment]').forEach(dated);
  root.querySelectorAll('.lines').forEach(lineChart);
  root.querySelectorAll('table:not(.hours)').forEach(sortable);
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

// Type-to-filter for long choice lists.
function filterable(list) {
  const input = Object.assign(document.createElement('input'), {type: 'search', className: 'filter', placeholder: 'Liste filtern …'});
  list.before(input);
  input.addEventListener('input', () => {
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
  });
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
      const found = q ? await (await fetch(`/datum/?q=${encodeURIComponent(q)}${'day' in input.dataset ? '&nur=tag' : ''}`)).json() : {};
      input.dataset.iso = found.iso || '';
      hint.textContent = q ? found.text || 'Nicht erkannt' : '';
      hint.classList.toggle('bad', Boolean(q && !found.text));
    }, 120);
  };
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

// Uploads show their progress, since videos are large; then the page goes where the form leads.
document.addEventListener('submit', event => {
  const form = event.target;
  if (!form.matches('[data-upload]') || form.closest('.drawer') || !form.checkValidity()) return;
  event.preventDefault();
  const button = form.querySelector('button');
  const request = new XMLHttpRequest();
  request.upload.addEventListener('progress', e => { button.textContent = `${Math.floor(e.loaded / e.total * 100)} % hochgeladen`; });
  request.addEventListener('load', () => location.assign(request.getResponseHeader('X-Location') || location.href));
  request.open('POST', form.action);
  request.setRequestHeader('X-Panel', '1');  // answered with where it leads instead of following, so the message is still there
  request.send(new FormData(form));
  button.disabled = true;
});

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
    if (response.status === 204) {
      stale = true;
      const next = new URL(response.headers.get('X-Location'), location.href);
      return next.pathname === new URL(page.href).pathname ? show(next) : history.back();
    }
    const html = response.headers.get('Content-Type')?.startsWith('text/html') && await response.text();
    if (!html || /^\s*<!doctype/i.test(html)) return location.assign(response.url);  // files, and pages of their own
    body.innerHTML = html;
    page.href = response.url;
    if (!drawer.classList.contains('open')) {
      history.pushState({drawer: true}, '');
      drawer.classList.add('open');
    }
    drawer.scrollTop = 0;
    init(body);
  };
  const close = () => drawer.classList.contains('open') && history.back();

  addEventListener('popstate', () => {
    if (stale) return location.reload();
    drawer.classList.remove('open', 'wide');
    wider.textContent = 'Breiter';
    body.innerHTML = '';
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
    if (inside && url.pathname === location.pathname) return close();  // back to the page underneath, e.g. 'Abbrechen'
    show(url);
  });
  document.addEventListener('submit', event => {
    const form = event.target;
    if (!drawer.contains(form)) return;
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
