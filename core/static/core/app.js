// Campus in the browser: forgiving list filters, dates in plain words, the search palette, the side panel and
// keyboard shortcuts. Everything works without it; it only removes clicks.

// Matching ignores case, accents and umlaut spellings, like the server does.
const fold = text => text.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase()
  .replace(/ß/g, 'ss').replace(/ae/g, 'a').replace(/oe/g, 'o').replace(/ue/g, 'u');

function init(root) {
  root.querySelectorAll('.form #id_modules, .form #id_staff, .form #id_participants').forEach(filterable);
  root.querySelectorAll('[data-day], [data-moment]').forEach(dated);
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
document.addEventListener('submit', async event => {
  const form = event.target;
  if (!inPlace(form) || event.submitter?.hasAttribute('formaction')) return;  // a button with its own target leaves the page
  event.preventDefault();
  const url = new URL(form.action);
  url.search = new URLSearchParams(new FormData(form));
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
});

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
