/* Collection Manager: API-backed navigation, collection review, and administration. */
const app = document.querySelector('#app');
const detailDialog = document.querySelector('#collection-dialog');
const createDialog = document.querySelector('#create-dialog');
const pages = ['overview', 'collections', 'drift', 'rotation', 'activity', 'settings'];
const pageNames = {overview: 'Overview', collections: 'Collections', drift: 'Drift', rotation: 'Rotation', activity: 'Activity', settings: 'Settings'};
const paths = {
  overview: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  collections: '<rect x="3" y="6" width="14" height="15" rx="2"/><path d="M7 3h12a2 2 0 0 1 2 2v12M7 10h6M7 14h4"/>',
  drift: '<path d="m12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6L12 3Z"/>',
  rotation: '<path d="M20 7v5h-5M4 17v-5h5M6.1 6.1a8 8 0 0 1 13 2.9M4.9 15a8 8 0 0 0 13 2.9"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  settings: '<path d="M4 7h9m4 0h3M4 17h3m4 0h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  film: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4"/>',
  tv: '<rect x="3" y="5" width="18" height="13" rx="2"/><path d="M8 21h8M12 18v3"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v.01"/>',
  home: '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1V10Z"/>',
  bookmark: '<path d="M6 3h12v18l-6-4-6 4V3Z"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  upload: '<path d="M12 16V4m-5 5 5-5 5 5M4 16v5h16v-5"/>',
  link: '<path d="m10 13 4-4m-6 6-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 3 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 1)"/>',
  user: '<circle cx="12" cy="8" r="3"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/>',
  logout: '<path d="M9 3H4v18h5M9 12h12m-5-5 5 5-5 5"/>',
  play: '<path d="m9 5 11 7-11 7V5Z"/>',
  archive: '<path d="M4 8h16v13H4V8ZM3 3h18v5H3V3Zm6 9h6"/>',
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.collections}</svg>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const num = value => Number(value || 0).toLocaleString();
const titleCase = value => String(value || '').replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
const list = value => Array.isArray(value) ? value : [];
const typeName = type => ['show', 'tv', 'series'].includes(type) ? 'TV shows' : 'Movies';
const typeIcon = type => ['show', 'tv', 'series'].includes(type) ? 'tv' : 'film';
let page = pages.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'overview';
let state = null;
let csrf = '';
let accountSetupRequired = false;
let authenticated = false;
let filter = 'all';
let query = '';
let detailId = null;
let detailTab = 'titles';
let settingsDirty = false;
let rotationEdits = null;
let refreshInFlight = null;
let refreshTimer;
let lastJobStates = new Map();
let lastStateFingerprint = '';
const collectionEdits = new Map();
const connectionOptionRequests = new Map();
const improvementJobs = new Map();

function savedCollectionFields(c) {
  return {name: c.name, description: c.description || '', titles: [...list(c.items), ...list(c.missing)].map(item => `${item.title} (${item.year})`).join('\n')};
}
function hasCollectionEdits(id) { return collectionEdits.has(String(id)); }
function updateCollectionEditControls() {
  const c = getCollection(detailId);
  if (!c) return;
  const dirty = hasCollectionEdits(c.id);
  for (const action of ['publish', 'apply-improvement', 'improve', 'request', 'request-all', 'add-available', 'describe', 'metadata']) {
    detailDialog.querySelectorAll(`[data-action="${action}"]`).forEach(el => {
      el.disabled = dirty || (['publish', 'apply-improvement'].includes(action) && !list(c.items).length);
    });
  }
  for (const id of ['detail-save-note', 'improvement-save-note']) {
    const note = detailDialog.querySelector(`#${id}`);
    if (note) note.hidden = !dirty;
  }
  const editNote = detailDialog.querySelector('#collection-edit-note');
  if (editNote) editNote.textContent = dirty ? 'Unsaved changes. Save before publishing or applying.' : 'Changes stay in your draft.';
  const discard = detailDialog.querySelector('[data-action="discard-edits"]');
  if (discard) { discard.hidden = !dirty; discard.style.display = dirty ? '' : 'none'; }
}
function refreshDetailIfSafe() {
  // The actual edit form owns its text and cursor until save. Read-only Options can refresh.
  if (detailDialog.open && !detailDialog.querySelector('#collection-edit-form')) renderDetail();
}
function refreshPagePreservingFocus() {
  const focused = document.activeElement;
  const searchFocused = focused?.id === 'collection-search';
  const selection = searchFocused ? [focused.selectionStart, focused.selectionEnd] : null;
  renderPage();
  if (searchFocused) {
    const search = document.querySelector('#collection-search');
    search?.focus({preventScroll: true});
    if (selection && search?.setSelectionRange) search.setSelectionRange(...selection);
  }
}
function refreshChrome() {
  const badge = document.querySelector('.connection-badge');
  if (badge) {
    const connected = !!state.library?.synced_at;
    badge.classList.toggle('disconnected', !connected);
    badge.innerHTML = `<i class="status-dot"></i>${connected ? 'Library connected' : 'Awaiting first sync'}`;
  }
  const driftLink = document.querySelector('.nav-link[data-page="drift"]');
  if (driftLink) {
    driftLink.querySelector('.nav-count')?.remove();
    if (candidates().length) driftLink.insertAdjacentHTML('beforeend', `<span class="nav-count">${candidates().length}</span>`);
  }
}

function date(value, short = false) {
  if (!value) return 'Not yet';
  const parsed = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
  if (Number.isNaN(parsed.getTime())) return 'Not yet';
  return new Intl.DateTimeFormat(undefined, short ? {month: 'short', day: 'numeric'} : {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}).format(parsed);
}
function relative(value) {
  if (!value) return 'Not yet';
  const parsed = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
  const minutes = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 60000));
  if (!Number.isFinite(minutes)) return 'Not yet';
  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`;
  if (minutes < 10080) return `${Math.floor(minutes / 1440)}d ago`;
  return date(value, true);
}
function toast(message, error = false) {
  const el = document.createElement('div');
  el.className = `toast${error ? ' error' : ''}`;
  el.innerHTML = `${icon(error ? 'info' : 'check')}<span>${esc(message)}</span><button class="icon-button" aria-label="Dismiss notification">${icon('close')}</button>`;
  el.querySelector('button').addEventListener('click', () => el.remove());
  document.querySelector('#toasts').append(el);
  setTimeout(() => el.remove(), error ? 14000 : 7000);
}
async function api(url, {method = 'GET', body} = {}) {
  const response = await fetch(url, {
    method, credentials: 'same-origin',
    headers: {'Accept': 'application/json', ...(body !== undefined ? {'Content-Type': 'application/json'} : {}), ...(method !== 'GET' ? {'X-CSRF-Token': csrf} : {})},
    ...(body !== undefined ? {body: JSON.stringify(body)} : {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && url !== '/api/login') { authenticated = false; renderLogin(); }
    throw new Error(typeof data.error === 'string' ? data.error : typeof data.message === 'string' ? data.message : `The request could not be completed (${response.status}).`);
  }
  return data;
}
function activeCollections() { return list(state?.collections).filter(c => c.status !== 'archived'); }
function candidates() { return activeCollections().filter(c => c.origin === 'drift' && c.status === 'draft'); }
function published() { return activeCollections().filter(c => c.status === 'published'); }
function permanentShelves() { return published().filter(c => !c.pinned_home && (c.origin !== 'drift' || c.permanent)); }
function temporaryDrift() { return published().filter(c => c.origin === 'drift' && !c.permanent); }
function activeDrift() {
  const identities = list(state?.drift?.active_batch_ids).map(String);
  return temporaryDrift().filter(c => identities.length ? identities.includes(String(c.id)) : c.home);
}
function shelfSlots() {
  const drift = Number(state.settings?.drift_slots ?? 4);
  return {movies: Number(state.settings?.permanent_movie_slots ?? 2), shows: Number(state.settings?.permanent_show_slots ?? 2), drift, driftTV: Math.min(drift, Number(state.settings?.drift_tv_slots ?? 2))};
}
function nextCycle(last, hours, enabled) {
  if (!enabled) return 'Manual refresh';
  if (!last) return 'Next: first scheduled refresh';
  const previous = new Date(typeof last === 'number' && last < 1e12 ? last * 1000 : last).getTime();
  const next = previous + Number(hours) * 3600000;
  return Number.isFinite(next) ? next <= Date.now() ? 'Next: refresh due' : `Next: ${date(next)}` : 'Next: first scheduled refresh';
}
function getCollection(id) { return list(state?.collections).find(c => String(c.id) === String(id)); }
function running(kind) { return list(state?.jobs).some(j => j.status === 'running' && (!kind || String(j.kind).toLowerCase().includes(kind === 'rotation' ? 'rotat' : kind))); }
function requested(item) { return !!(item.requested || item.requested_at || /^(requested|already in)/i.test(item.request_status || '')); }
function requestable(item) { return !requested(item) && item.reason !== 'Ambiguous library match' && item.metadata_status !== 'needs_check' && !['weak', 'uncertain'].includes(item.fit_status); }
function pill(c) {
  if (c.status === 'published') return `<span class="pill live"><i class="status-dot"></i>${c.managed === false ? 'From Plex' : 'Live in Plex'}</span>`;
  if (c.status === 'kept') return '<span class="pill kept">Saved draft</span>';
  if (c.status === 'archived') return '<span class="pill">Archived</span>';
  return '<span class="pill draft">Draft</span>';
}
function button(label, action, {kind = '', id = '', glyph = '', small = false, disabled = false, extra = ''} = {}) {
  return `<button class="button ${kind} ${small ? 'small' : ''}" data-action="${esc(action)}"${id ? ` data-id="${esc(id)}"` : ''}${disabled ? ' disabled' : ''} ${extra}>${glyph ? icon(glyph) : ''}${esc(label)}</button>`;
}
function cover(c, position = 0) {
  const hash = [...String(c.name || c.id)].reduce((n, char) => n + char.charCodeAt(0), 0);
  const artwork = list(c.items).filter(item => item.has_art).slice(0, 3);
  return `<div class="cover theme-${hash % 6}${artwork.length ? ' has-art' : ''}" aria-hidden="true">
    ${artwork.length ? `<div class="cover-art">${Array.from({length: 3}, (_, i) => `<img src="/api/artwork/${encodeURIComponent(artwork[i % artwork.length].id)}" loading="lazy" alt="">`).join('')}</div>` : '<span class="cover-orbit"></span><span class="cover-shape"></span>'}
    <div class="cover-top"><span class="eyebrow">${c.origin === 'drift' ? 'A Drift collection' : 'From your library'}</span>${icon(c.origin === 'drift' ? 'drift' : typeIcon(c.media_type))}</div>
    <div class="cover-title">${esc(c.name || 'Untitled collection')}</div><span class="cover-number">${String(position + 1).padStart(2, '0')} / ${typeName(c.media_type).toUpperCase()}</span>
  </div>`;
}
function collectionCard(c, index = 0, review = false) {
  return `<article class="collection-card">
    <button class="collection-open" data-action="open" data-id="${esc(c.id)}" aria-label="Review ${esc(c.name)}">
      ${cover(c, index)}
      <div class="collection-meta"><div><h3>${esc(c.name)}</h3><p>${num(list(c.items).length)} titles <span aria-hidden="true">·</span> ${typeName(c.media_type)}${c.home && c.status === 'published' ? ' · On Home' : ''}</p></div>${pill(c)}</div>
    </button>
    ${review ? `<p class="card-description">${esc(c.thesis || c.description || 'Open this proposal to explore the connection between its titles.')}</p><div class="card-review-actions">${button('Review collection', 'open', {id: c.id, kind: 'secondary', small: true})}${button('Dismiss', 'archive', {id: c.id, kind: 'ghost', small: true})}</div>` : ''}
  </article>`;
}
function driftCard(c, index = 0) {
  return `<div>${collectionCard(c, index)}<div class="card-review-actions">${button('Keep in permanent rotation', 'keep', {id: c.id, kind: 'secondary', glyph: 'bookmark', small: true})}</div></div>`;
}
function empty(title, body, action = '', actionLabel = '', glyph = 'collections') {
  return `<div class="empty-state"><div class="empty-icon">${icon(glyph)}</div><h2>${esc(title)}</h2><p>${esc(body)}</p>${action ? button(actionLabel, action, {glyph: action === 'settings' ? 'settings' : action === 'generate' ? 'drift' : 'plus'}) : ''}</div>`;
}
function heading(title, body, actions = '', eyebrow = '') {
  return `<header class="page-heading"><div>${eyebrow ? `<span class="eyebrow">${esc(eyebrow)}</span>` : ''}<h1>${esc(title)}</h1><p>${esc(body)}</p></div>${actions ? `<div class="heading-actions">${actions}</div>` : ''}</header>`;
}
function navLink(name) {
  return `<a class="nav-link" href="#${name}" data-page="${name}" ${page === name ? 'aria-current="page"' : ''} aria-label="${pageNames[name]}">${icon(name)}<span>${pageNames[name]}</span>${name === 'drift' && candidates().length ? `<span class="nav-count">${candidates().length}</span>` : ''}</a>`;
}
function renderShell() {
  if (!state) return;
  const connected = !!state.library?.synced_at;
  app.innerHTML = `<div class="app-shell">
    <aside class="sidebar"><a href="#overview" class="brand" aria-label="Collection Manager overview"><div class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></div><div class="brand-name">Collection<small>Manager</small></div></a><div class="nav-label">YOUR LIBRARY, REIMAGINED</div><nav aria-label="Main navigation">${pages.filter(p => p !== 'settings').map(navLink).join('')}</nav><div class="sidebar-footer">${navLink('settings')}<div class="sidebar-note"><span><i class="status-dot"></i>Your library. Your collections.</span>Made for discovering more.</div></div></aside>
    <div class="workspace"><header class="topbar"><div class="breadcrumb"><span>Library</span><span aria-hidden="true">/</span><strong id="page-crumb">${pageNames[page]}</strong></div><div class="topbar-actions"><span class="connection-badge ${connected ? '' : 'disconnected'}"><i class="status-dot"></i>${connected ? 'Library connected' : 'Awaiting first sync'}</span><button class="avatar" data-action="settings" aria-label="Open settings">${icon('user')}</button></div></header><main class="page" id="main" tabindex="-1"><div id="jobs-bar" class="jobs-bar" aria-live="polite"></div><div id="page-content"></div></main></div>
  </div>`;
  renderPage();
}
function renderJobs() {
  const target = document.querySelector('#jobs-bar');
  if (!target || !state) return;
  const labels = {sync: 'Refreshing your library', library_sync: 'Refreshing your library', drift: 'Curating new collections', generate: 'Curating new collections', rotation: 'Rotating your Home shelves', publish: 'Publishing to Plex', improve: 'Finding stronger connections', request: 'Sending your request', import: 'Importing collections'};
  target.innerHTML = list(state.jobs).filter(j => j.status === 'running').map(j => `<div class="job"><span class="spinner" aria-hidden="true"></span><strong>${esc(labels[j.kind] || titleCase(j.kind))}</strong><p>${esc(j.message || 'Working on it…')}</p></div>`).join('');
}
function renderPage() {
  const target = document.querySelector('#page-content');
  if (!target || !state) return;
  document.title = `${pageNames[page]} · Collection Manager`;
  document.querySelector('#page-crumb').textContent = pageNames[page];
  document.querySelectorAll('.nav-link[data-page]').forEach(el => {
    if (el.dataset.page === page) el.setAttribute('aria-current', 'page');
    else el.removeAttribute('aria-current');
  });
  const renderers = {overview: renderOverview, collections: renderCollections, drift: renderDrift, rotation: renderRotation, activity: renderActivity, settings: renderSettings};
  target.innerHTML = renderers[page]();
  renderJobs();
  if (page === 'settings') loadConnectionOptions();
}
function heroArtwork() {
  const seen = new Set();
  const items = activeCollections().flatMap(c => list(c.items)).filter(item => item.has_art && !seen.has(item.id) && seen.add(item.id)).slice(0, 3);
  const words = ['A familiar<br>favourite.', 'A new<br>perspective.', 'An unexpected<br>connection.'];
  return `<div class="hero-art" aria-hidden="true"><div class="hero-orbit"></div><div class="hero-books">${Array.from({length: 3}, (_, i) => `<div class="hero-book">${items[i] ? `<img src="/api/artwork/${encodeURIComponent(items[i].id)}" alt="">` : `<span class="hero-book-label">COLLECTION / MANAGER</span><span class="hero-book-circle"></span><span class="hero-book-mark">${words[i]}</span>`}</div>`).join('')}</div><span class="hero-caption">${items.length ? 'From your collection' : 'A fresh way into your library'}</span></div>`;
}
function renderOverview() {
  const hasLibrary = Number(state.library?.count) > 0;
  const drafts = candidates();
  const liveDrift = activeDrift();
  const permanentHome = permanentShelves().filter(c => c.home);
  const slots = shelfSlots();
  const featured = [...published(), ...activeCollections().filter(c => c.status !== 'published')].slice(0, 3);
  return `<header class="page-heading overview-heading"><div><h1>Your library, thoughtfully collected.</h1><p>A good collection makes the next watch feel obvious.</p></div>${button('New collection', 'create', {kind: 'secondary', glyph: 'plus'})}</header>
    <section class="hero" aria-labelledby="hero-title"><div class="hero-copy"><span class="eyebrow">${icon('drift')}${hasLibrary ? 'FAMILIAR TITLES. UNEXPECTED CONNECTIONS.' : 'A NEW CHAPTER FOR YOUR LIBRARY'}</span><h2 id="hero-title">${hasLibrary ? 'Rediscover what’s<br>already yours.' : 'Great collections<br>start here.'}</h2><p>${hasLibrary ? 'Your permanent favourites take turns on Home. Drift brings a fresh set of discoveries alongside them. Keep the ones you love.' : 'Connect your library. Find the stories that belong together. Give your Plex Home a little more personality.'}</p><div class="actions">${hasLibrary ? button(liveDrift.length || drafts.length ? 'Explore Drift' : 'Refresh Drift', liveDrift.length || drafts.length ? 'drift' : 'generate', {glyph: 'drift', disabled: running('drift')}) : button('Connect your library', 'settings', {glyph: 'arrow'})}${button(hasLibrary ? 'Browse collections' : 'Import collections', hasLibrary ? 'collections' : 'import-dialog', {kind: 'ghost', glyph: hasLibrary ? 'arrow' : 'download'})}</div></div>${heroArtwork()}</section>
    ${arrivalSuggestions().length ? `<section class="quiet-panel section"><h3>${num(arrivalSuggestions().length)} new collection suggestions</h3><p>New arrivals may belong on your existing shelves.</p>${button('Review new arrivals','collections',{kind:'secondary',small:true})}</section>` : ''}
    <div class="stats-strip" aria-label="Library summary">${[
      ['film', num(state.library?.count), 'Titles in your library'],
      ['collections', `${num(permanentHome.length)} / ${num(slots.movies + slots.shows)}`, 'Permanent shelves on Home'],
      ['drift', `${num(liveDrift.filter(c => c.home).length)} / ${num(slots.drift)}`, 'Drift shelves on Home'],
      ['bookmark', num(drafts.length), 'Drift drafts awaiting review'],
    ].map(([glyph, value, label]) => `<div class="stat"><div class="stat-icon">${icon(glyph)}</div><div><div class="stat-value">${value}</div><p class="stat-label">${label}</p></div></div>`).join('')}</div>
    ${!hasLibrary ? `<section class="section"><div class="section-heading"><h2>Make yourself at home</h2></div><div class="setup-steps"><article class="setup-step"><span class="step-number">01</span><h3>Bring your library</h3><p>Connect Plex, then sync the films and shows you already have.</p><button class="text-link" data-action="settings">Open connections ${icon('arrow')}</button></article><article class="setup-step"><span class="step-number">02</span><h3>Find the connection</h3><p>Explore Drift’s ideas or build a collection around a theme of your own.</p><button class="text-link" data-action="create">Create a collection ${icon('arrow')}</button></article><article class="setup-step"><span class="step-number">03</span><h3>Give it a shelf</h3><p>Review the titles, publish to Plex, and keep your Home fresh with rotation.</p><button class="text-link" data-action="rotation">Explore rotation ${icon('arrow')}</button></article></div></section>` : ''}
    ${featured.length ? `<section class="section"><div class="section-heading"><div><div class="title-line"><h2>${published().length ? 'In your collection' : 'Taking shape'}</h2><span class="count-badge">${activeCollections().length}</span></div><p>${published().length ? 'Familiar favourites, with a new way in.' : 'Your drafts are ready for a closer look.'}</p></div><button class="text-link" data-action="collections">View all ${icon('arrow')}</button></div><div class="collection-grid overview-grid">${featured.map((c, i) => collectionCard(c, i)).join('')}</div></section>` : hasLibrary ? `<section class="section">${empty('Your first collection is waiting to happen', 'Start with a theme you love, import a list, or let Drift find a connection in your library.', 'generate', 'Let Drift explore', 'drift')}</section>` : ''}
    <section class="section overview-bottom"><article class="quiet-panel"><span class="eyebrow">FRESH DISCOVERIES</span><h3>${liveDrift.length ? `${num(liveDrift.length)} Drift shelves live now` : 'Collections with a point of view'}</h3><p>Drift generates a pool of up to ${num(state.settings?.drift_pool_size ?? 12)} collections every ${num(state.settings?.drift_generation_hours ?? 168)} hours. ${num(slots.drift)} shelves take turns on Home every ${num(state.settings?.drift_interval_hours ?? 48)} hours. Household viewing offers a light nudge.</p><button class="text-link" data-action="drift">Explore Drift ${icon('arrow')}</button></article><article class="quiet-panel"><span class="eyebrow">YOUR PERMANENT FAVOURITES</span><h3>A little change goes a long way.</h3><p>${num(slots.movies)} movie shelves and ${num(slots.shows)} TV shelves take turns on Home alongside Drift. Keep a Drift collection to make it part of that permanent rotation.</p><button class="text-link" data-action="rotation">Manage rotation ${icon('arrow')}</button></article></section>`;
}
function filteredCollections() {
  return list(state.collections).filter(c => ['archived', 'drift-history'].includes(filter) ? c.status === 'archived' : c.status !== 'archived').filter(c => {
    if (filter === 'drift-history' && c.origin !== 'drift') return false;
    if (filter === 'published' && c.status !== 'published') return false;
    if (filter === 'draft' && !['draft', 'kept'].includes(c.status)) return false;
    if (filter === 'movies' && ['show', 'tv', 'series'].includes(c.media_type)) return false;
    if (filter === 'shows' && !['show', 'tv', 'series'].includes(c.media_type)) return false;
    return `${c.name} ${c.description || ''} ${c.thesis || ''}`.toLowerCase().includes(query.toLowerCase());
  });
}
function collectionResults() {
  const found = filteredCollections();
  return `<p class="browse-summary" aria-live="polite">${num(found.length)} ${found.length === 1 ? 'collection' : 'collections'}${query ? ` matching “${esc(query)}”` : ''}</p>${found.length ? `<div class="collection-grid">${found.map((c, i) => collectionCard(c, i)).join('')}</div>` : empty(query || filter !== 'all' ? 'No collections match just yet' : 'Build something worth browsing', query || filter !== 'all' ? 'Try another search or choose a different filter.' : 'Gather titles around an idea, import a list, or explore what Drift finds in your library.', query || filter !== 'all' ? '' : 'create', 'Create a collection')}`;
}
function arrivalSuggestions(collectionId = '') {
  return list(state.new_arrivals?.suggestions).filter(row => row.status === 'pending' && (!collectionId || row.collection_id === String(collectionId)));
}
function arrivalSuggestionRow(row) {
  const c = getCollection(row.collection_id);
  return `<div class="media-item" style="display:grid;grid-template-columns:34px minmax(0,1fr)" data-arrival-suggestion="${esc(row.id)}"><div class="item-thumb">${icon(typeIcon(row.media_type))}</div><div class="item-info"><strong>${esc(row.title)} (${esc(row.year)})</strong><p>New in Plex &middot; ${esc(c?.name || row.collection_name)}</p><p class="fit-reason">${esc(row.reason)}</p>${c?.managed === false ? '<p class="detail-note">Open the collection and choose Manage rotation here before adding.</p>' : ''}</div><div class="item-side" style="grid-column:2;margin-left:0;display:flex;flex-wrap:wrap;gap:6px">${button('Add to collection','arrival-add',{id:row.id,kind:'secondary',small:true,disabled:running() || !c?.managed})}${button('Open collection','open',{id:row.collection_id,kind:'ghost',small:true})}${button('Dismiss','arrival-dismiss',{id:row.id,kind:'ghost',small:true,disabled:running()})}</div></div>`;
}
function renderArrivalInbox() {
  const inbox = state.new_arrivals || {};
  const rows = arrivalSuggestions();
  const enabled = !!state.settings?.advanced?.new_arrival_suggestions;
  return `<section class="quiet-panel section" data-arrivals-inbox><div class="section-heading"><div><h2>New arrivals, familiar shelves</h2><p>${num(rows.length)} suggestions &middot; ${num(inbox.pending_count)} titles awaiting review</p></div>${button('Review new arrivals','arrival-review',{kind:'secondary',small:true,disabled:running() || !inbox.pending_count})}</div><label class="toggle-row"><span class="toggle-copy"><span>Suggest homes for new movies and shows</span><small>Compare new Plex titles with permanent collections once a day. Uses your curator model; you choose what gets added.</small></span><span class="switch"><input type="checkbox" data-arrivals-toggle ${enabled ? 'checked' : ''} aria-label="Suggest homes for new movies and shows"><span class="switch-track"></span></span></label>${inbox.error ? `<p class="form-error" role="status">${esc(inbox.error)}</p>` : ''}${rows.length ? `<details><summary>${num(rows.length)} additions to review</summary><div class="item-list">${rows.map(arrivalSuggestionRow).join('')}</div></details>` : `<p class="detail-note">${inbox.last_review_at ? `Last reviewed ${esc(date(inbox.last_review_at))}. No suggestions waiting.` : 'Your existing library is the starting point. New titles are noticed during Plex sync; new episodes and file upgrades do not trigger repeat suggestions.'}</p>`}</section>`;
}
function renderCollections() {
  return `${heading('Collections', 'A place for the stories that belong together.', button('Review permanent collections', 'collection-sweep', {kind:'secondary',glyph:'drift',disabled:running()}) + button('Import', 'import-dialog', {kind: 'secondary', glyph: 'download'}) + button('New collection', 'create', {glyph: 'plus'}))}<div class="browse-toolbar"><div class="filter-group" aria-label="Filter collections">${[['all','All collections'],['published','Published'],['draft','Drafts'],['movies','Movies'],['shows','TV shows'],['archived','Archived'],['drift-history','Drift history']].map(([value, label]) => `<button class="filter-chip" data-filter="${value}" aria-pressed="${filter === value}">${label}</button>`).join('')}</div><label class="search-field">${icon('search')}<span class="sr-only">Search collections</span><input id="collection-search" type="search" placeholder="Find a collection…" value="${esc(query)}" autocomplete="off"></label></div>${state.collection_review ? `<details class="settings-details"><summary>${icon('drift')}<span><strong>Collection review</strong><small>${esc(date(state.collection_review.created_at))} - suggestions only</small></span>${icon('chevron')}</summary><div class="advanced-body"><p class="detail-note">${esc(state.collection_review.summary)}</p>${list(state.collection_review.collections).map(row => `<details class="section"><summary>${esc(row.name)}</summary><p class="detail-note">${esc(row.flavour)}</p><p class="detail-note">${esc(row.advice)}</p>${list(row.additions).map(item=>`<div class="media-item"><div class="item-info"><strong>${esc(item.title)} (${esc(item.year)})</strong><p>${esc(item.reason)}</p></div></div>`).join('')}${button('Open collection','open',{id:row.id,kind:'ghost',small:true})}</details>`).join('')}</div></details>` : ''}${renderArrivalInbox()}<div id="collection-results">${collectionResults()}</div>`;
}
function renderDrift() {
  const poolIds = new Set(list(state.drift?.pool_ids));
  const drafts = activeCollections().filter(c => c.origin === 'drift' && !c.permanent && !c.home &&
    (poolIds.has(c.id) || c.status === 'draft'));
  const saved = activeCollections().filter(c => c.origin === 'drift' && c.status === 'kept');
  const live = activeDrift();
  const slots = shelfSlots();
  const settings = state.settings || {};
  const automatic = !!settings.advanced?.auto_publish;
  const scheduled = !!settings.advanced?.drift_schedule_enabled;
  const interval = settings.drift_interval_hours ?? 48;
  const diagnostics = state.drift?.diagnostics || {};
  const opportunities = list(state.drift?.opportunities);
  return `${heading('A fresh perspective.', `${num(slots.drift - slots.driftTV)} movie shelves and ${num(slots.driftTV)} TV shelves alongside your permanent favourites.`, button('Drift history', 'drift-history', {kind: 'secondary', glyph: 'clock'}) + button('Switch shelves', 'switch-drift', {kind: 'secondary', glyph: 'rotation', disabled: running('drift') || !list(state.drift?.pool_ids).length}) + button('Generate weekly pool', 'generate', {glyph: 'drift', disabled: running('drift') || !state.library?.count}), 'DRIFT')}
    <section class="quiet-panel" style="margin-bottom:20px"><label class="toggle-row" style="border:0;padding:0"><span class="toggle-copy"><span>Seasonal awareness</span><small>${settings.advanced?.seasonal_enabled ? (list(state.seasonal?.active).map(e => `${esc(e.label)} until ${esc(e.end)}`).join(' ? ') || 'On. No seasonal occasion active today.') : 'Off. Drift explores your library without a seasonal bias.'} Brisbane calendar; seasonal shelves receive Home priority during their window.</small></span><span class="switch"><input type="checkbox" data-seasonal-toggle ${settings.advanced?.seasonal_enabled ? 'checked' : ''} aria-label="Seasonal awareness"><span class="switch-track"></span></span></label>${button('Choose occasions', 'settings', {kind:'ghost', small:true})}</section>
    <section class="drift-intro"><div><span class="eyebrow">CURATED AROUND AN IDEA</span><h2>Discover it. Love it. Keep it.</h2><p>Drift aims for ${num(settings.drift_pool_size ?? 12)} collections per pool, generated every ${num(settings.drift_generation_hours ?? 168)} hours. ${num(slots.drift)} take turns on Home every ${num(interval)} hours. ${automatic ? 'Reviewed choices stay available throughout the week. Shelves publish when their turn comes; previous pools move into history. Useful partial results are kept.' : 'New collections stay as drafts until you publish them. Automatic publishing is off.'} Keep a favourite to promote it into permanent rotation. Recent viewing offers inspiration without creating shelves for individual users.</p><div class="drift-meta"><span>${icon('clock')}${esc(nextCycle(state.drift?.last_activated_at, interval, scheduled))}</span><span>${icon('rotation')}${scheduled ? `Every ${num(interval)} hours` : 'Schedule off'}</span><span>${icon('check')}${automatic ? 'Auto-publish after review' : 'Publish manually'}</span><span>${icon('collections')}${num(live.length)} live · ${num(drafts.length)} queued</span></div></div><div class="icon-emblem" aria-hidden="true">${icon('drift')}</div></section>
    ${diagnostics.needs_attention ? '<p class="detail-note" role="status">Scheduled exploration is paused after repeated unsuccessful batches. Check the details below, then refresh Drift manually to resume.</p>' : ''}
    ${live.length ? `<section class="section" data-drift-live><div class="section-heading"><div><h2>Live now</h2><p>These temporary collections stay separate from your permanent rotation. Keep any you want to bring along.</p></div><span class="count-badge">${num(live.length)} / ${num(slots.drift)}</span></div><div class="collection-grid">${live.map(driftCard).join('')}</div></section>` : ''}
    ${!state.library?.count ? empty('Give Drift a library to explore', 'Connect Plex and sync your library. Drift needs real titles before it can find meaningful connections.', 'settings', 'Connect your library', 'drift') : !live.length && !drafts.length ? empty('A fresh set of discoveries belongs here', automatic ? 'Refresh Drift to create and review a new batch. Qualifying collections are published automatically alongside your permanent shelves.' : 'Refresh Drift to create new drafts. Review and publish the collections you want on Home.', 'generate', 'Refresh Drift', 'drift') : ''}
    ${drafts.length ? `<section class="section"><div class="section-heading"><div><h2>More from this week</h2><p>${automatic ? 'Reviewed choices waiting for their turn on Home. Open one to explore, improve or keep it.' : 'Automatic publishing is off. Review a proposal before publishing it.'}</p></div><span class="count-badge">${num(drafts.length)}</span></div><div class="collection-grid">${drafts.map((c, i) => collectionCard(c, i, true)).join('')}</div></section>` : ''}
    ${saved.length ? `<section class="section"><div class="section-heading"><div><h2>Kept for permanent rotation</h2><p>These saved drafts will join your permanent pool once published.</p></div></div><div class="collection-grid">${saved.map((c, i) => collectionCard(c, i)).join('')}</div></section>` : ''}
    ${opportunities.length ? `<section class="section"><div class="section-heading"><div><h2>Ideas to grow into</h2><p>Promising themes that need more titles before they earn a shelf.</p></div></div><div class="collection-grid">${opportunities.map(idea => `<article class="quiet-panel"><span class="eyebrow">${esc(typeName(idea.media_type))}</span><h3>${esc(idea.name || idea.concept)}</h3><p>${esc(idea.description || idea.thesis)}</p><p class="detail-note">${num(idea.owned_count)} owned · ${num(list(idea.missing).length)} missing</p>${button('Review missing titles', 'save-opportunity', {id: idea.id, kind: 'secondary', small: true})}</article>`).join('')}</div></section>` : ''}
    ${diagnostics.reason ? `<details class="quiet-panel section"><summary>Last exploration · ${diagnostics.publishable ? `${num(diagnostics.selected)} shelves ready` : 'No new batch published'}</summary><p>${esc(diagnostics.reason)}</p>${diagnostics.usage ? `<p class="detail-note">${esc(diagnostics.usage.model)} ? ${num(diagnostics.usage.calls)} model calls ? ${num(diagnostics.usage.input_tokens)} input / ${num(diagnostics.usage.output_tokens)} output tokens ? Estimated US$${Number(diagnostics.usage.estimated_usd || 0).toFixed(3)} (before tax)</p>` : ""}${diagnostics.watch_signal_status === 'unavailable' ? '<p class="detail-note">Recent viewing was unavailable. Drift used library evidence on its own.</p>' : ''}${list(diagnostics.rejected).length ? `<ul>${diagnostics.rejected.map(row => `<li><strong>${esc(row.concept || 'Candidate')}</strong> — ${esc(row.reason)}</li>`).join('')}</ul>` : ''}</details>` : ''}`;
}
function renderFamilyShelf() {
  const config = state.family_shelf;
  const c = config && getCollection(config.shelf_id);
  if (!config) return `<details class="quiet-panel section"><summary>Pin a recent family movie shelf</summary><p>Keep the 25 newest family arrivals on Home, for ages 10?16 and adults. Older picks roll into a collection you choose. Family fit is assessed automatically using your curator model.</p><form id="family-shelf-form"><div class="field-stack">${field('name','Shelf name','First Dibs on the Sofa')}<label class="field"><span>Collection for older family movies</span><select name="destination_id" required><option value="">Choose a collection</option>${permanentShelves().filter(c=>c.media_type==='movie' && c.managed).map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</select></label></div><button class="button secondary" type="submit">Create and fill pinned shelf</button><p class="form-error" id="family-error" role="alert"></p></form></details>`;
  return `<section class="quiet-panel section" data-family-shelf><div class="section-heading"><div><span class="eyebrow">PINNED FAMILY MOVIE NIGHT</span><h2>${esc(c?.name || 'Family shelf')}</h2><p>${num(list(c?.items).length)} / 25 movies &middot; ${config.enabled ? 'Updates automatically after Plex sync' : 'Automatic updates paused'}</p></div></div><p>The newest arrivals for ages 10?16 and the grown-ups. Older films move into ${esc(getCollection(config.destination_id)?.name || 'your family collection')}. This shelf stays on Home alongside your rotating shelves.</p><div class="actions">${button('Open shelf','open',{id:config.shelf_id,kind:'secondary',small:true})}${button('Refresh family shelf','family-refresh',{kind:'secondary',small:true,disabled:running()})}${button(config.enabled ? 'Pause automatic updates' : 'Resume automatic updates','family-toggle',{kind:'ghost',small:true,disabled:running()})}</div>${config.error ? `<p class="form-error">${esc(config.error)}</p>` : ''}</section>`;
}
function renderRotation() {
  const shelves = permanentShelves();
  const inPool = shelves.filter(c => c.managed && c.rotation_enabled);
  const onHome = shelves.filter(c => c.home);
  const liveDrift = activeDrift();
  const slots = shelfSlots();
  const settings = state.settings || {};
  return `${heading('Keep Home moving.', 'Permanent favourites and temporary discoveries, side by side.', button('Rotate permanent shelves', 'rotate', {glyph: 'rotation', disabled: (!inPool.length && !onHome.some(c => c.managed)) || running('rotation')}), 'ROTATION')}
    <section class="drift-intro"><div><span class="eyebrow">ROOM FOR BOTH</span><h2>Up to ${num(slots.movies + slots.shows + slots.drift)} shelves. Two different rhythms.</h2><p>Your permanent collection pool supplies ${num(slots.movies)} movie shelves and ${num(slots.shows)} TV shelves. Drift adds ${num(slots.drift)} newly curated shelves alongside them, then replaces its own batch on a separate schedule.</p><div class="drift-meta"><span>${icon('collections')}${num(inPool.length)} permanent collections in rotation</span><span>${icon('drift')}${num(liveDrift.length)} active Drift collections</span><button class="text-link" data-action="settings">Automation settings ${icon('arrow')}</button></div></div><div class="icon-emblem" aria-hidden="true">${icon('rotation')}</div></section>
    ${renderFamilyShelf()}
    ${rotationSetup()}
    <section class="section" data-permanent-home><div class="section-heading"><div><h2>Permanent shelves</h2><p>${num(onHome.filter(c => typeIcon(c.media_type) === 'film').length)} / ${num(slots.movies)} movies · ${num(onHome.filter(c => typeIcon(c.media_type) === 'tv').length)} / ${num(slots.shows)} TV shows · ${esc(nextCycle(state.last_rotation_at, settings.rotation_hours ?? 24, settings.advanced?.schedule_enabled))}</p></div></div>${onHome.length ? `<div class="collection-grid">${onHome.map((c, i) => collectionCard(c, i)).join('')}</div>` : empty('Your favourites have room here', 'Add published collections to the permanent pool, then rotate them onto Home.', '', '', 'collections')}</section>
    <section class="section" data-drift-live><div class="section-heading"><div><h2>Drift shelves</h2><p>${num(liveDrift.filter(c => c.home).length)} / ${num(slots.drift)} on Home · ${esc(nextCycle(state.drift?.last_activated_at, settings.drift_interval_hours ?? 48, settings.advanced?.drift_schedule_enabled))}</p></div>${button('Generate weekly pool', 'generate', {kind: 'secondary', glyph: 'drift', disabled: running('drift') || !state.library?.count})}</div>${liveDrift.length ? `<div class="collection-grid">${liveDrift.map(driftCard).join('')}</div>` : empty('A little space for discovery', 'Refresh Drift to create a batch of temporary collections alongside your permanent shelves.', '', '', 'drift')}</section>
    <section class="section" data-permanent-pool><div class="section-heading"><div><h2>Permanent rotation pool</h2><p>Choose the collections that can take a permanent movie or TV slot. Kept Drift collections belong here too.</p></div></div>${!shelves.length ? empty('Start your permanent pool', 'Publish a collection or keep a Drift favourite to give your permanent shelves something to show.', 'collections', 'Browse collections', 'home') : `<div class="collection-grid">${shelves.map((c, i) => `<article class="collection-card"><button class="collection-open" data-action="open" data-id="${esc(c.id)}" aria-label="Review ${esc(c.name)}">${cover(c, i)}<div class="collection-meta"><div><h3>${esc(c.name)}</h3><p>${num(list(c.items).length)} titles${c.home ? ' · Currently on Home' : c.managed ? ' · Permanent collection' : ' · Imported from Plex'}</p></div></div></button>${c.managed ? rotationToggle(c) : `<div class="card-review-actions">${button('Manage rotation here', 'adopt', {id: c.id, kind: 'secondary', small: true, glyph: 'rotation'})}</div>`}</article>`).join('')}</div>`}</section>`;
}
function rotationSetup() {
  const s = {...state.settings, ...rotationEdits};
  return `<section class="quiet-panel" aria-labelledby="rotation-setup-title"><div class="section-heading"><div><h2 id="rotation-setup-title">Make room for what you love</h2><p>Choose the number of shelves and how often each group changes.</p></div></div><form id="rotation-form"><div class="field-grid rotation-fields">${field('permanent_movie_slots', 'Permanent movie shelves', s.permanent_movie_slots ?? 2, {type: 'number', min: 0, max: 20})}${field('permanent_show_slots', 'Permanent TV shelves', s.permanent_show_slots ?? 2, {type: 'number', min: 0, max: 20})}${field('drift_slots', 'Temporary Drift shelves', s.drift_slots ?? 4, {type: 'number', min: 2, max: 20})}${field('drift_tv_slots', 'TV shelves within Drift', s.drift_tv_slots ?? 2, {type: 'number', min: 0, max: s.drift_slots ?? 4, hint: `${Math.max(0, Number(s.drift_slots ?? 4) - Number(s.drift_tv_slots ?? 2))} remaining Drift shelves are movies.`})}${field('rotation_hours', 'Rotate permanent shelves every (hours)', s.rotation_hours ?? 24, {type: 'number', min: 1, max: 8760, hint: '24 = daily. Uses the collections in your permanent pool.'})}${field('drift_pool_size', 'Collections to generate per week', s.drift_pool_size ?? 12, {type: 'number', min: 4, max: 20, hint: 'A larger pool to browse and rotate. Good partial results are kept.'})}${field('drift_generation_hours', 'Generate a new pool every (hours)', s.drift_generation_hours ?? 168, {type: 'number', min: 1, max: 8760, hint: '168 = weekly. Previous pools remain in Drift history.'})}${field('drift_interval_hours', 'Switch Drift shelves every (hours)', s.drift_interval_hours ?? 48, {type: 'number', min: 1, max: 8760, hint: '48 = every two days. Rotates existing choices without generating again.'})}</div><div class="actions" style="margin-top:20px"><button class="button" type="submit">Save rotation setup</button><span class="muted" id="rotation-save-note" style="font-size:11px">${rotationEdits ? 'You have unsaved changes.' : 'Changes apply on the next rotation or Drift refresh.'}</span></div><p class="form-error" id="rotation-error" role="alert"></p></form></section>`;
}
function rotationToggle(c) {
  if (c.pinned_home) return '<p class="detail-note">Pinned to Home independently of rotation.</p>';
  return `<label class="toggle-row" style="margin-top:12px;padding:12px 0"><span class="toggle-copy"><span>Include in rotation</span></span><span class="switch"><input type="checkbox" data-rotation="${esc(c.id)}" ${c.rotation_enabled ? 'checked' : ''} aria-label="Include ${esc(c.name)} in rotation"><span class="switch-track"></span></span></label>`;
}
function renderActivity() {
  const entries = list(state.activity);
  return `${heading('Behind the scenes', 'A clear record of what changed, and what needs your attention.', button('Refresh', 'refresh', {kind: 'secondary', glyph: 'rotation'}))}${entries.length ? `<section class="panel activity-list" aria-label="Recent activity">${entries.map(entry => `<article class="activity-row"><div class="activity-symbol ${['error', 'failed'].includes(entry.level) ? 'error' : ''}">${icon(['error', 'failed'].includes(entry.level) ? 'info' : 'check')}</div><div class="activity-content"><p>${esc(entry.message)}</p><time>${esc(date(entry.time))}</time></div></article>`).join('')}</section>` : empty('A clean slate', 'Your library syncs, new proposals, published collections, and rotation updates will appear here.', '', '', 'activity')}`;
}
function field(key, label, value = '', {secret = false, saved = false, placeholder = '', type = 'text', hint = '', full = false, min = '', max = ''} = {}) {
  return `<label class="field${full ? ' full' : ''}"><span>${esc(label)}</span><input name="${esc(key)}" type="${secret ? 'password' : type}" value="${secret ? '' : esc(value)}" placeholder="${esc(saved && secret ? 'Saved — leave blank to keep' : placeholder)}" ${secret ? 'autocomplete="new-password"' : 'autocomplete="off"'}${min !== '' ? ` min="${esc(min)}"` : ''}${max !== '' ? ` max="${esc(max)}"` : ''}><small>${esc(hint || (secret && saved ? 'A credential is stored. Leave blank to keep it.' : ''))}</small></label>`;
}
function connectionSelect(service, kind, label, value) {
  const key = `${service}_${kind}`;
  const current = String(value ?? '');
  const configured = !!state.settings?.[`has_${service}_key`];
  const placeholder = kind === 'root' ? 'Choose a folder' : 'Choose a quality profile';
  return `<label class="field"><span>${esc(label)}</span><select name="${key}" data-connection-options="${service}" data-option-kind="${kind}" aria-describedby="${key}-hint"><option value="">${placeholder}</option>${current ? `<option value="${esc(current)}" selected>${esc(kind === 'root' ? current : 'Saved quality profile')}</option>` : ''}</select><small id="${key}-hint" role="status">${configured ? `Loading choices from ${titleCase(service)}…` : `Save your ${titleCase(service)} connection to load choices.`}</small></label>`;
}
async function loadConnectionOptions() {
  await Promise.all(['radarr', 'sonarr'].map(loadServiceOptions));
}
async function loadServiceOptions(service) {
  const form = document.querySelector('#settings-form');
  if (!form || !state.settings?.[`has_${service}_key`]) return;
  const selects = [...form.querySelectorAll(`[data-connection-options="${service}"]`)];
  const request = (connectionOptionRequests.get(service) || 0) + 1;
  connectionOptionRequests.set(service, request);
  for (const select of selects) {
    select.setAttribute('aria-busy', 'true');
    form.querySelector(`#${select.name}-hint`).textContent = `Loading choices from ${titleCase(service)}…`;
  }
  try {
    const data = await api(`/api/connections/${service}/options`);
    if (!form.isConnected || connectionOptionRequests.get(service) !== request) return;
    for (const select of selects) {
      // Read at completion: users may change a selection while the request runs.
      const selected = select.value;
      const root = select.dataset.optionKind === 'root';
      const choices = root ? list(data.roots).filter(row => typeof row.path === 'string' && row.path).map(row => [row.path, row.path]) : list(data.profiles).filter(row => row.id !== undefined && typeof row.name === 'string').map(row => [String(row.id), row.name]);
      const options = [new Option(root ? 'Choose a folder' : 'Choose a quality profile', '')];
      const seen = new Set(['']);
      for (const [value, label] of choices) {
        if (!seen.has(value)) { options.push(new Option(label, value)); seen.add(value); }
      }
      const unavailable = selected && !seen.has(selected);
      if (unavailable) options.push(new Option(root ? `${selected} (unavailable)` : 'Saved quality profile — unavailable', selected));
      select.replaceChildren(...options);
      select.value = selected;
      form.querySelector(`#${select.name}-hint`).textContent = unavailable ? `Your selection is retained but was not returned by ${titleCase(service)}.` : choices.length ? `Choose from ${root ? 'folders' : 'quality profiles'} configured in ${titleCase(service)}.` : `No ${root ? 'root folders' : 'quality profiles'} are configured in ${titleCase(service)} yet.`;
    }
  } catch (error) {
    if (!form.isConnected || connectionOptionRequests.get(service) !== request) return;
    for (const select of selects) {
      form.querySelector(`#${select.name}-hint`).textContent = `Couldn’t load choices from ${titleCase(service)}. Your selection is unchanged. Use Save & test to retry.`;
    }
  } finally {
    if (form.isConnected && connectionOptionRequests.get(service) === request) selects.forEach(select => select.removeAttribute('aria-busy'));
  }
}
function toggle(key, label, description, checked) {
  return `<label class="toggle-row"><span class="toggle-copy"><span>${esc(label)}</span><small>${esc(description)}</small></span><span class="switch"><input type="checkbox" name="advanced.${esc(key)}" ${checked ? 'checked' : ''}><span class="switch-track"></span></span></label>`;
}
function renderSettings() {
  const s = state.settings || {};
  const a = s.advanced || {};
  const service = (name, key, fields, configured, hint) => `<section class="service-section"><div class="service-heading"><div class="service-name"><i class="service-indicator ${configured ? 'configured' : ''}" aria-hidden="true"></i><strong>${name}</strong><small>${hint || (configured ? 'Configured' : 'Optional')}</small></div>${button('Save & test', 'test', {kind: 'secondary', small: true, id: key})}</div><div class="field-grid">${fields}</div></section>`;
  settingsDirty = false;
  return `${heading('Make it yours.', 'Your connections and preferences, all in one place.', '', 'SETTINGS')}
    <div class="settings-layout"><form id="settings-form">
      <section class="settings-section panel"><div class="settings-section-heading"><div><h2>Your library</h2><p>Start with Plex. The other connections add more ways to discover.</p></div>${icon('link')}</div>
      ${service('Plex', 'plex', field('plex_url', 'Server URL', s.plex_url, {placeholder: 'http://plex:32400'}) + field('plex_token', 'Plex token', '', {secret: true, saved: s.has_plex_token}) + field('plex_movie_lib', 'Movie library', s.plex_movie_lib, {placeholder: 'Movies'}) + field('plex_tv_lib', 'TV library', s.plex_tv_lib, {placeholder: 'TV Shows'}), s.has_plex_token, s.has_plex_token ? 'Configured' : 'Required')}
      <div class="data-actions"><div><h3>Refresh library & collections</h3><p>${state.library?.synced_at ? `Last synced ${esc(date(state.library.synced_at))}` : 'Bring your titles and existing Plex collections into the app.'}</p></div>${button('Sync library', 'sync', {kind: 'secondary', glyph: 'rotation', small: true, disabled: running('sync')})}</div>
      ${service('Tautulli', 'tautulli', field('tautulli_url', 'Server URL', s.tautulli_url, {placeholder: 'http://tautulli:8181'}) + field('tautulli_key', 'API key', '', {secret: true, saved: s.has_tautulli_key}), s.has_tautulli_key, 'Household inspiration')}
      </section>
      <section class="settings-section panel"><div class="settings-section-heading"><div><h2>Discovery & requests</h2><p>Find stronger themes and request missing titles from the app.</p></div>${icon('drift')}</div>
      ${service('Theme & naming model', 'llm', field('llm_url', 'OpenAI-compatible API URL', s.llm_url, {placeholder: 'http://ollama:11434/v1', hint: 'Use an API that supports chat completions.'}) + field('llm_model', 'Model name', s.llm_model, {placeholder: 'Your installed model'}) + field('llm_key', 'API key', '', {secret: true, saved: s.has_llm_key, full: true, hint: 'Only needed when your provider requires one.'}), !!s.llm_url, 'Collection ideas & titles')}
      ${service('Radarr', 'radarr', field('radarr_url', 'Server URL', s.radarr_url, {placeholder: 'http://radarr:7878'}) + field('radarr_key', 'API key', '', {secret: true, saved: s.has_radarr_key}) + connectionSelect('radarr', 'root', 'Movie folder', s.radarr_root) + connectionSelect('radarr', 'profile', 'Quality profile', s.radarr_profile), s.has_radarr_key, 'Movie requests')}
      ${service('Sonarr', 'sonarr', field('sonarr_url', 'Server URL', s.sonarr_url, {placeholder: 'http://sonarr:8989'}) + field('sonarr_key', 'API key', '', {secret: true, saved: s.has_sonarr_key}) + connectionSelect('sonarr', 'root', 'TV folder', s.sonarr_root) + connectionSelect('sonarr', 'profile', 'Quality profile', s.sonarr_profile), s.has_sonarr_key, 'TV requests')}
      <section class="service-section"><div class="service-heading"><div class="service-name"><i class="service-indicator ${s.has_trakt_client_id ? 'configured' : ''}" aria-hidden="true"></i><strong>Trakt</strong><small>Public list imports</small></div></div>${field('trakt_client_id', 'Trakt application client ID', '', {secret: true, saved: s.has_trakt_client_id, hint: 'Used to read the public Trakt lists you import.'})}</section>
      </section>
      <details class="settings-details" id="seasonal-settings"><summary>${icon('drift')}<span><strong>Seasonal awareness</strong><small>Occasions and Queensland school holidays.</small></span>${icon('chevron')}</summary><div class="advanced-body">
      ${toggle('seasonal_enabled','Enable seasonal awareness','Bias Drift generation and Home rotation toward active occasions. Schedule and auto-publish controls still apply.',!!a.seasonal_enabled)}
      ${[['halloween','Halloween','17-31 October: primarily spooky seasonal shelves.'],['christmas','Christmas','11-25 December: primarily festive shelves.'],['easter','Easter','Seven days before Easter Sunday through Easter Monday.'],['st_patrick',"St Patrick's Day",'14-17 March: a few Irish stories and creative voices.'],['qld_school','Queensland school holidays','More choices for ages 10-16. Uses state-school dates; ratings and stories inform curation, not a parental-control guarantee.'],['new_year','New Year','29 December-1 January: fresh starts and reinvention.'],['valentine',"Valentine's Day",'11-14 February: different kinds of romance.'],['star_wars','May the Fourth','2-4 May: Star Wars and distinct space adventures.']].map(([key,label,note]) => toggle('seasonal_'+key,label,note,a['seasonal_'+key] ?? !['new_year','valentine','star_wars'].includes(key))).join('')}
      <p class="detail-note">Dates use Australia/Brisbane. Queensland holiday dates are published through October 2029; later dates will need updating. ${state.seasonal?.school_calendar_current === false ? 'School-holiday coverage is unavailable for the current date.' : ''} A changed occasion can trigger one new pool; ordinary switching does not make AI calls.</p></div></details>
      <details class="settings-details" id="advanced-settings"><summary>${icon('settings')}<span><strong>Advanced settings</strong><small>Fine-tune what runs, what gets suggested, and what goes on Home.</small></span>${icon('chevron')}</summary><div class="advanced-body">
      ${toggle('sync_enabled', 'Keep the library and requested arrivals up to date', 'Refresh Plex automatically and track requested arrivals. Drift additions pass a fresh fit review before joining a live collection.', a.sync_enabled !== false)}
      ${field('library_sync_minutes', 'Library refresh interval (minutes)', s.library_sync_minutes ?? 10, {type:'number', min:5, max:1440})}
      ${toggle('new_arrival_suggestions', 'Suggest collection homes for new arrivals', 'Review newly synced movies and series once a day against your permanent collections. Suggestions wait for your approval.', !!a.new_arrival_suggestions)}
      ${toggle('watch_inspiration', 'Take a little inspiration from household viewing', 'Recent viewing can suggest themes. Collections are never named for, or built around, individual users.', a.watch_inspiration !== false)}
      ${toggle('schedule_enabled', 'Rotate permanent shelves automatically', 'Choose new movie and TV shelves from your permanent pool on its own schedule.', !!a.schedule_enabled)}
      ${toggle('drift_schedule_enabled', 'Refresh Drift automatically', 'Generate a weekly pool and switch through its saved collections on your Drift schedule.', !!a.drift_schedule_enabled)}
      ${toggle('auto_publish', 'Auto-publish reviewed Drift', 'Publish reviewed shelves when their turn on Home arrives. Turn off to publish drafts yourself.', a.auto_publish !== false)}
      ${toggle('home_enabled', 'Manage Plex Home shelves', 'Allow published collections to appear on your own Plex Home.', a.home_enabled !== false)}
      ${toggle('shared_home', 'Also feature shelves for shared users', 'Apply Home visibility to the users you share your Plex libraries with.', !!a.shared_home)}
      ${toggle('missing_suggestions', 'Suggest titles beyond my library', 'Show missing titles that could strengthen a collection. Requests happen only when you choose them.', a.missing_suggestions !== false)}
      ${toggle('diversity', 'Keep each batch varied', 'Favour distinct themes and avoid filling a new batch with similar collections.', a.diversity !== false)}
      <div class="advanced-fields"><p class="detail-note">Shelf counts and refresh intervals are together on the Rotation page.</p><button type="button" class="text-link" data-action="rotation">Adjust rotation setup ${icon('arrow')}</button></div>
      </div></details>
      <div class="settings-save"><p id="settings-save-note">Credentials stay on your server.</p><button type="submit" class="button">${icon('check')}Save settings</button></div><p class="form-error" id="settings-error" role="alert"></p>
    </form>
    <section class="settings-section panel section"><div class="settings-section-heading"><div><h2>Your data</h2><p>Bring your previous work with you and keep a copy of your collections.</p></div>${icon('archive')}</div><div class="data-actions"><div><h3>Import previous Collection Manager data</h3><p>Read the legacy source mounted on this server.</p></div>${button('Import', 'legacy-import', {kind: 'secondary', small: true})}</div><div class="data-actions"><div><h3>Export collections</h3><p>Download a portable backup of your collection data.</p></div><a class="button secondary small" href="/api/export" download>${icon('download')}Export</a></div><div class="data-actions"><div><h3>Session</h3><p>Sign out of Collection Manager on this browser.</p></div>${button('Sign out', 'logout', {kind: 'ghost', small: true})}</div></section></div>`;
}
function renderLogin(error = '') {
  const setup = accountSetupRequired;
  app.innerHTML = `<main id="main" class="login-screen"><section class="login-card"><div class="brand"><div class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></div><div class="brand-name">Collection<small>Manager</small></div></div><h1>${setup ? 'Make it yours.' : 'Welcome back.'}</h1><p>${setup ? 'Choose your username and password to get started.' : 'Sign in to your library.'}</p><form id="login-form"><label class="field"><span>Username</span><input name="username" autocomplete="username" maxlength="64" required autofocus></label><label class="field"><span>Password</span><input name="password" type="password" autocomplete="${setup ? 'new-password' : 'current-password'}" ${setup ? 'minlength="12"' : ''} required></label>${setup ? '<label class="field"><span>Confirm password</span><input name="confirm_password" type="password" autocomplete="new-password" minlength="12" required></label><p class="detail-note">Use at least 12 characters for your password.</p>' : ''}<button class="button" type="submit">${setup ? 'Create account' : 'Open your library'} ${icon('arrow')}</button><p class="form-error" id="login-error" role="alert">${esc(error)}</p></form></section><p class="login-note">Your library. Your collections.</p></main>`;
  document.querySelector('[name=username]')?.focus();
}
function mediaItem(item, missing = false, index = 0, collection = null) {
  const alreadyRequested = requested(item);
  const fitReason = item.fit_reason || item.reason || list(collection?.review?.item_reviews).find(review => String(review.id) === String(item.id))?.reason;
  return `<div class="media-item"><div class="item-thumb">${item.has_art ? `<img src="/api/artwork/${encodeURIComponent(item.id)}" alt="" loading="lazy">` : icon(typeIcon(item.media_type || collection?.media_type))}</div><div class="item-info"><strong>${esc(item.title || item.name || 'Untitled')}</strong><p>${esc(item.year || typeName(item.media_type || collection?.media_type))}</p>${fitReason ? `<p class="fit-reason">${esc(fitReason)}</p>` : ''}${item.request_error ? `<p class="detail-note">${esc(item.request_error)}</p>` : ''}${missing && item.metadata_status === 'unavailable' ? '<p class="detail-note">Catalog lookup unavailable. This is a connection problem, not an unresolved title. Retry Check metadata.</p>' : ''}${missing && item.metadata_status === 'needs_check' ? '<p class="detail-note">No unique catalog match yet. Check the title/year or retry metadata lookup.</p>' : ''}${missing && item.metadata?.summary ? `<details class="detail-note"><summary>About this title</summary><p>${esc(item.metadata.summary)}</p></details>` : ''}${list(item.possible_matches).length ? `<p class="detail-note">Check library match: ${esc(item.possible_matches.map(row => `${row.title} (${row.year || 'year unknown'}) ? ${typeName(row.media_type)}`).join('; '))}. Correct the title or year before requesting.</p>` : ''}</div><div class="item-side">${missing ? alreadyRequested ? `<span class="pill kept">${esc(item.request_progress || (/^already in/i.test(item.request_status || '') ? 'Already in Arr' : 'Requested'))}</span>` : item.request_progress === 'Requesting' ? '<span class="pill">Requesting...</span>' : requestable(item) ? button(`Request in ${typeIcon(collection?.media_type) === 'tv' ? 'Sonarr' : 'Radarr'}`, 'request', {id: collection.id, kind: 'secondary', small: true, extra: `data-index="${index}"`}) : `<span class="pill">${item.fit_status === 'weak' ? 'Review fit' : 'Needs checking'}</span>` : (collection?.status === 'published' && list(collection.items).some(row => row.id === item.id) ? 'In collection' : 'In Plex')}</div></div>`;
}
function arrivalNote(c) {
  const service = typeIcon(c.media_type) === 'tv' ? 'Sonarr' : 'Radarr';
  return `<p class="detail-note">Requests go to ${service} and may start downloads. ${state.settings?.advanced?.sync_enabled !== false ? 'Requested titles are tracked and added when Plex sees them. Drift additions also pass a fresh fit review.' : 'Automatic library refresh is off; sync your library to check requested arrivals. Drift additions also need a fresh fit review.'}${c.origin === 'improve' ? ' Apply or publish this draft to add arrivals to Plex.' : ''}</p>`;
}
function availableItem(item, c) {
  return `<div class="media-item"><div class="item-thumb">${icon(typeIcon(item.media_type || c.media_type))}</div><div class="item-info"><strong>${esc(item.title)}</strong><p>${esc(item.year)}</p>${item.reason ? `<p class="fit-reason">${esc(item.reason)}</p>` : ''}${item.arrival_review_error ? `<p class="form-error" role="status">Addition paused: ${esc(item.arrival_review_error)} You can retry the review.</p>` : ''}</div><div class="item-side"><span class="pill">In Plex</span>${button(item.arrival_review_error ? 'Retry addition' : 'Add to collection', 'add-available', {id: c.id, kind: 'secondary', small: true, extra: `data-item-id="${esc(item.id)}"`})}</div></div>`;
}
function inlineImprovements(c) {
  const pending = activeCollections().filter(row => String(row.source_collection_id) === String(c.id) && row.origin === 'improve' && ['draft', 'kept'].includes(row.status)).sort((a, b) => Number(b.created_at || 0) - Number(a.created_at || 0));
  const job = list(state.jobs).find(row => row.id === improvementJobs.get(String(c.id)));
  const progress = job?.status === 'running' ? `<div class="job" role="status"><span class="spinner" aria-hidden="true"></span><strong>Finding improvements</strong><p>${esc(job.message || 'Looking for strong fits...')}</p></div>` : job?.status === 'failed' ? `<p class="detail-note" role="status">${esc(job.message)}</p>` : '';
  const key = item => `${String(item.title || '').toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()}|${item.year}`;
  const seen = new Set(list(c.items).map(key));
  const owned = [], missing = [];
  // Prefer newest notes, but retain older choices and each row's action target.
  for (const owner of [...pending, c]) {
    const candidates = [...(owner === c ? [] : list(owner.items)), ...list(owner.available)];
    for (const item of candidates) {
      if (seen.has(key(item))) continue;
      seen.add(key(item)); owned.push({item, owner, available: list(owner.available).some(row => row.id === item.id)});
    }
  }
  for (const suggestion of arrivalSuggestions(c.id)) {
    const item = {title:suggestion.title, year:suggestion.year};
    if (!seen.has(key(item))) { seen.add(key(item)); owned.push({suggestion}); }
  }
  for (const owner of [...pending, c]) {
    list(owner.missing).forEach((item, index) => {
      if (seen.has(key(item))) return;
      seen.add(key(item)); missing.push({item, owner, index});
    });
  }
  const draft = pending[0];
  if (!owned.length && !missing.length && !draft) return progress;
  const bulk = draft || c;
  return `${progress}<section class="section" data-collection-suggestions ${draft ? `data-improvement-preview="${esc(draft.id)}"` : ''}><div class="section-heading"><div><h3>Could complete the picture</h3><p>${num(owned.length)} already in your library &middot; ${num(missing.length)} not in your library</p></div><div class="actions">${draft ? button('Review improvements', 'open', {id:draft.id, kind:'secondary', small:true}) : ''}${missing.some(row => requested(row.item)) ? button('Refresh progress', 'request-progress', {kind:'ghost', small:true}) : ''}${missing.length ? button('Check metadata', 'metadata', {id:c.id, kind:'secondary', small:true}) : ''}${missing.length && missing.every(row => row.owner.id === bulk.id) && missing.some(row => requestable(row.item)) ? button('Request all missing', 'request-all', {id:bulk.id, kind:'secondary', small:true}) : ''}</div></div>${draft ? '<p class="detail-note">Suggestions from your improvements are gathered here. Your live collection stays unchanged until you apply a reviewed draft.</p>' : ''}<h3>Already in your library</h3>${owned.length ? `<div class="item-list">${owned.map(row => row.suggestion ? arrivalSuggestionRow(row.suggestion) : row.available ? availableItem(row.item, row.owner) : mediaItem(row.item, false, 0, row.owner)).join('')}</div>` : '<p class="detail-note">No new library matches yet.</p>'}<h3 style="margin-top:20px">Not in your library</h3>${missing.length ? `${arrivalNote(bulk)}<div class="item-list">${missing.map(row => mediaItem(row.item, true, row.index, row.owner)).join('')}</div>` : '<p class="detail-note">No external suggestions yet.</p>'}</section>`;
}

function collectionOptions(c) {
  if (c.family_rolling) return `<h3>A fresh family movie night</h3><p class="detail-note">This pinned shelf automatically keeps the 25 newest suitable family arrivals. Older films roll into your family collection. Controls are on Rotation.</p>${button('Manage family shelf','rotation',{kind:'secondary'})}`;
  if (c.status === 'archived') return '<p class="detail-note">This collection is archived. Its history stays available here.</p>';
  if (c.status === 'published' || c.origin === 'drift') {
    return `<h3>Explore a stronger version</h3><p class="detail-note">${c.origin === 'drift' ? 'The name, theme, and titles were reviewed together. Explore an improvement to create a new proposal with a fresh point of view.' : 'Create an improvement draft to refine the theme and find titles that could complete the picture.'} The original collection stays available.</p>${button('Create an improvement draft', 'improve', {id: c.id, kind: 'secondary', glyph: 'drift'})}${c.status === 'published' ? `<section class="section"><h3>${c.origin === 'drift' && !c.permanent ? 'A temporary discovery' : 'Permanent rotation'}</h3><p class="detail-note">${c.origin === 'drift' && !c.permanent ? 'This collection belongs to Drift’s temporary shelves. Keep it to promote it into your permanent movie or TV pool.' : c.managed ? 'Choose whether this collection takes a permanent slot on Plex Home. Rotate permanent shelves to update Home.' : 'Let Collection Manager include this collection in permanent rotation. Its existing titles are preserved.'}</p>${c.origin === 'drift' && !c.permanent ? button('Keep in permanent rotation', 'keep', {id: c.id, kind: 'secondary', glyph: 'bookmark'}) : c.managed ? rotationToggle(c) : button('Manage rotation here', 'adopt', {id: c.id, kind: 'secondary', glyph: 'rotation'})}</section>` : ''}`;
  }
  const fields = collectionEdits.get(String(c.id)) || savedCollectionFields(c);
  return `<form id="collection-edit-form" data-id="${esc(c.id)}"><div class="field-stack"><label class="field"><span>Collection name</span><input name="name" value="${esc(fields.name)}" required minlength="2" maxlength="100"></label><label class="field"><span>Description</span><textarea name="description" rows="4" maxlength="2000">${esc(fields.description)}</textarea></label><label class="field"><span>Titles · one Title (Year) per line</span><textarea name="titles" rows="10" required>${esc(fields.titles)}</textarea></label></div><div class="actions" style="margin-top:20px"><button class="button" type="submit">${c.origin === 'opportunity' && !c.manual_reviewed ? 'Save reviewed draft' : 'Save changes'}</button><span class="muted" id="collection-edit-note" style="font-size:11px">${hasCollectionEdits(c.id) ? 'Unsaved changes. Save before publishing or applying.' : 'Changes stay in your draft.'}</span>${button('Discard edits', 'discard-edits', {id: c.id, kind: 'ghost', small: true, extra: hasCollectionEdits(c.id) ? '' : 'hidden'})}</div><p class="form-error" id="edit-error" role="alert"></p></form>`;
}
function improvementAction(c) {
  const source = getCollection(c.source_collection_id);
  const applicable = source?.status === 'published' && source.managed;
  const dirty = hasCollectionEdits(c.id);
  const promotesDrift = source?.origin === 'drift' && !source.permanent;
  return `<div class="quiet-panel section"><h3>${promotesDrift ? 'Keep this stronger version' : 'Update the original shelf'}</h3><p>${applicable ? `Apply the saved titles and description to “${esc(source.name)}”. Its name stays the same, and removed titles stay in your Plex library.${promotesDrift ? ' Applying this edit promotes the collection from temporary Drift into your permanent rotation pool.' : ''}` : source?.status === 'published' ? 'First choose “Manage rotation here” on the original collection. Then return to apply this edit.' : 'The original collection is not a managed Plex shelf. Publish this draft as a separate collection, or publish and manage the original first.'}</p>${applicable ? button(promotesDrift ? 'Apply and keep permanently' : 'Apply to original', 'apply-improvement', {id: c.id, kind: 'secondary', glyph: 'check', disabled: dirty || !list(c.items).length}) : source ? button('Open original collection', 'open', {id: source.id, kind: 'secondary', glyph: 'arrow'}) : ''}<p class="detail-note" id="improvement-save-note" ${dirty ? '' : 'hidden'}>Save your edits before applying them to the original.</p></div>`;
}
function renderDetail() {
  const c = getCollection(detailId);
  if (!c) return;
  const missing = list(c.missing);
  const available = list(c.available);
  const dirty = hasCollectionEdits(c.id);
  const needsReview = c.origin === 'opportunity' && !c.manual_reviewed;
  detailDialog.innerHTML = `<div class="dialog-top">${cover(c)}<button class="icon-button dialog-close" data-action="close-detail" aria-label="Close collection">${icon('close')}</button><div class="dialog-heading">${pill(c)}<h2 id="dialog-title">${esc(c.name)}</h2><p>${esc(c.description || c.thesis || `${num(list(c.items).length)} titles, brought together in one collection.`)}</p><div class="actions">${c.status !== 'published' && c.status !== 'archived' ? needsReview ? button('Review before publishing', 'review-opportunity', {id: c.id, glyph: 'check'}) : button(c.origin === 'improve' ? 'Publish as a new collection' : 'Publish to Plex', 'publish', {id: c.id, glyph: 'upload', disabled: dirty || !list(c.items).length}) : ''}${!c.permanent && c.origin === 'drift' ? button('Keep in permanent rotation', 'keep', {id: c.id, kind: 'secondary', glyph: 'bookmark'}) : ''}${c.status !== 'archived' && !c.family_rolling ? button('Improve collection', 'improve', {id: c.id, kind: 'secondary', glyph: 'drift', disabled: dirty}) : ''}</div><p id="detail-save-note" class="detail-note" ${dirty ? '' : 'hidden'}>You have unsaved edits. Save them in Edit collection before publishing or applying.</p>${needsReview ? '<p class="detail-note">This idea needs your review. Check its name and titles, then save it before publishing.</p>' : ''}</div></div>
      <div class="dialog-body"><div class="dialog-tabs" role="tablist" aria-label="Collection details">${[['titles', `Titles · ${list(c.items).length}`], ['story', 'The connection'], ['edit', c.status === 'published' || c.origin === 'drift' ? 'Options' : 'Edit collection']].map(([key, label]) => `<button class="dialog-tab" id="tab-${key}" role="tab" aria-selected="${detailTab === key}" aria-controls="detail-panel" data-tab="${key}" tabindex="${detailTab === key ? '0' : '-1'}">${label}</button>`).join('')}</div><section id="detail-panel" role="tabpanel" aria-labelledby="tab-${detailTab}" tabindex="0">
      ${detailTab === 'titles' ? `${list(c.items).length ? `<div class="item-list">${list(c.items).map(item => mediaItem(item, false, 0, c)).join('')}</div>` : '<p class="detail-note">No matching titles are in your synced library yet.</p>'}${inlineImprovements(c)}` : ''}
      ${detailTab === 'story' ? `<div class="thesis"><span class="eyebrow">THE THREAD THAT HOLDS IT TOGETHER</span><p>${esc(c.thesis || c.description || 'This collection was brought together by its curator. Add a description to share the idea behind it.')}</p></div>${c.name_reason ? `<h3>Why this name</h3><p class="detail-note">${esc(c.name_reason)}</p>` : ''}<h3>Collection notes</h3><p class="detail-note">Created ${esc(date(c.created_at))}${c.published_at ? `. Published ${esc(date(c.published_at))}` : '. This collection has not been published to Plex.'}${c.permanent ? ' Kept as a permanent collection.' : ''}</p>${c.source_collection_id ? '<p class="detail-note">This is a new proposal based on an existing collection. The original collection is preserved.</p>' : ''}` : ''}
      ${detailTab === 'story' && c.origin !== 'drift' && c.status !== 'archived' ? `<p class="detail-note">Refresh the explanation of how these titles fit together.</p>${button('Refresh connection', 'describe', {id: c.id, kind: 'secondary', glyph: 'drift', disabled: dirty})}` : ''}
      ${detailTab === 'story' && c.changes ? `<h3>Changes from the original</h3><p class="detail-note"><strong>Add:</strong> ${esc(list(c.changes.added).join(', ') || 'No additions')}<br><strong>Remove from collection:</strong> ${esc(list(c.changes.removed).join(', ') || 'No removals')}</p>` : ''}
      ${detailTab === 'edit' ? collectionOptions(c) : ''}
      ${c.origin === 'improve' && ['draft', 'kept'].includes(c.status) ? improvementAction(c) : ''}
      </section><footer class="dialog-bottom"><p>${c.status === 'published' ? 'Current collection is live in Plex; proposed additions are separate until applied.' : c.status === 'archived' ? 'Archived in Collection Manager.' : 'A local draft. Nothing has been published yet.'}</p>${c.status !== 'archived' ? button(c.status === 'published' ? 'Retire from rotation' : 'Dismiss proposal', 'archive', {id: c.id, kind: 'ghost', small: true, glyph: 'archive'}) : ''}</footer></div>`;
  updateCollectionEditControls();
}
function openDetail(id) {
  detailId = id; detailTab = 'titles'; renderDetail();
  if (!detailDialog.open) detailDialog.showModal();
}
function openCreate() {
  createDialog.innerHTML = `<h2 id="create-title">Create a collection.</h2><p>Paste a list from anywhere, or let the curator build one from your library.</p><div class="actions" style="margin-bottom:20px">${button('Paste a list', 'create', {kind:'secondary'})}${button('Generate an idea', 'discover-dialog', {kind:'ghost', glyph:'drift'})}</div><form id="create-form"><div class="field-stack">${field('name', 'Collection name (optional)', '', {placeholder:'Use the first line of your pasted list'})}<label class="field"><span>Library</span><select name="media_type"><option value="movie">Movies</option><option value="show">TV shows</option></select></label><label class="field"><span>Paste your collection</span><textarea name="text" rows="8" placeholder="Unwanted Passengers
Alien (1979)
The Thing (1982)
Life (2017)" required></textarea><small>The first line can be the collection name. Numbered lists, bullets and Title (Year) lines are accepted. Include a year for reliable matching.</small></label><label class="field"><span>Description (optional)</span><textarea name="description" rows="2" maxlength="2000"></textarea></label><label class="toggle-row"><span class="toggle-copy"><span>Request missing titles automatically</span><small>Send unmatched titles to Radarr/Sonarr after importing. This can start downloads.</small></span><input type="checkbox" name="auto_request"></label></div><p class="form-error" id="create-error" role="alert"></p><div class="actions">${button('Cancel','close-create',{kind:'ghost'})}<button class="button" type="submit">Import and review ${icon('arrow')}</button></div></form>`;
  createDialog.querySelector('[name=name]').maxLength = 100;
  createDialog.showModal();
}
function openDiscovery(sourceId = '') {
  const source = sourceId ? list(state.collections).find(c => c.id === sourceId) : null;
  createDialog.innerHTML = `<h2 id="create-title">${source ? `Improve ${esc(source.name)}` : 'Find your next collection.'}</h2><p>${source ? 'Find overlooked titles, fill the gaps, or tighten the selection. Review the changes before applying them.' : 'Describe a collection, or leave the idea blank for a surprise drawn from your library.'}</p><form id="discovery-form" data-source-id="${esc(sourceId)}"><div class="field-stack">${source ? '' : '<label class="field"><span>Library</span><select name="media_type"><option value="movie">Movies</option><option value="show">TV shows</option></select></label>'}<label class="field"><span>Where to find picks</span><select name="mode"><option value="library">Only titles in my library</option><option value="expand">My library + external picks</option></select></label>${source ? '<label class="field"><span>What should improve?</span><select name="goal"><option value="expand">Expand with similar picks</option><option value="gaps">Fill important gaps</option><option value="quality">Improve quality and fit</option><option value="normalize">Make the selection more consistent</option></select></label>' : ''}<label class="field"><span>${source ? 'Additional direction (optional)' : 'Your idea (optional)'}</span><textarea name="prompt" rows="3" maxlength="2000" placeholder="${source ? 'More overlooked films, keep the original mood?' : 'Small-town mysteries with a supernatural edge?'}"></textarea><small>${source ? 'The original collection stays unchanged until you apply the reviewed draft.' : 'Leave blank for a random collection idea. This creates a separate draft, outside automatic Drift.'}</small></label>${field('limit', 'Maximum suggestions',12,{type:'number',min:3,max:30})}<label class="toggle-row"><span class="toggle-copy"><span>Request missing picks automatically</span><small>Available with external picks. Approved suggestions are sent to Radarr/Sonarr and can start downloads.</small></span><input type="checkbox" name="auto_request" disabled></label></div><p class="form-error" id="discovery-error" role="alert"></p><div class="actions">${button('Cancel','close-create',{kind:'ghost'})}<button class="button" type="submit">${source ? 'Find improvements' : 'Generate collection'} ${icon('drift')}</button></div></form>`;
  createDialog.showModal();
}
function openImport() {
  createDialog.innerHTML = `<h2 id="create-title">Bring your collections.</h2><p>Start with something you already love. Every import is available here to review.</p><div class="field-stack"><div class="quiet-panel"><span class="eyebrow">FROM YOUR SERVER</span><h3>Existing Plex collections</h3><p>Sync the collections already in Plex alongside your library.</p>${button('Sync from Plex', 'sync', {kind: 'secondary', glyph: 'rotation'})}</div><div class="quiet-panel"><span class="eyebrow">FROM A LIST</span><h3>Paste a few titles</h3><p>Turn a list of films or shows into a collection draft.</p>${button('Add titles', 'create', {kind: 'secondary', glyph: 'plus'})}</div><div class="quiet-panel"><span class="eyebrow">FROM TRAKT</span><h3>A list worth keeping</h3><p>Import a public Trakt list and see what is already in your library.</p><form id="trakt-form"><div class="field-stack">${field('url', 'Public Trakt list URL', '', {placeholder: 'https://trakt.tv/users/…/lists/…', type: 'url'})}${field('name', 'Collection name (optional)', '', {placeholder: 'Use the list name'})}<label class="field"><span>Library</span><select name="media_type"><option value="movie">Movies</option><option value="show">TV shows</option></select></label></div><p class="form-error" id="trakt-error" role="alert"></p><button class="button secondary" type="submit" style="margin-top:16px">Import list ${icon('arrow')}</button></form></div></div><div class="actions">${button('Done', 'close-create', {kind: 'ghost'})}</div>`;
  createDialog.querySelector('[name=url]').required = true;
  createDialog.showModal();
}
async function refresh({render = true, quiet = false} = {}) {
  // A write must receive fresh state even when it finishes during an earlier poll.
  while (refreshInFlight) await refreshInFlight;
  if (!authenticated) return;
  refreshInFlight = fetchState({render, quiet});
  try { await refreshInFlight; }
  finally { refreshInFlight = null; }
}
async function fetchState({render, quiet}) {
  try {
    const next = await api('/api/state');
    const fingerprint = JSON.stringify([next.collections, next.library, next.family_shelf, next.new_arrivals, next.drift, next.activity, next.settings, next.last_rotation_at, list(next.jobs).map(job => [job.id, job.status])]);
    let completed = false;
    for (const job of list(next.jobs)) {
      const previousStatus = lastJobStates.get(job.id);
      if ((previousStatus === 'running' || (!previousStatus && state)) && ['succeeded', 'failed'].includes(job.status)) {
        toast(job.message || (job.status === 'succeeded' ? 'All done.' : 'That operation could not be completed.'), job.status === 'failed');
        completed = true;
      }
    }
    lastJobStates = new Map(list(next.jobs).map(job => [job.id, job.status]));
    const changed = fingerprint !== lastStateFingerprint;
    lastStateFingerprint = fingerprint;
    state = next;
    if (!document.querySelector('.app-shell')) renderShell();
    else if (page !== 'settings' && !(page === 'rotation' && rotationEdits) && (render || changed || completed)) refreshPagePreservingFocus();
    else renderJobs();
    refreshChrome();
    if (changed || completed) refreshDetailIfSafe();
    document.querySelectorAll('img').forEach(img => img.addEventListener('error', () => { img.style.display = 'none'; }, {once: true}));
  } catch (error) {
    if (!quiet) toast(error.message, true);
    if (!state && authenticated) app.innerHTML = `<main class="boot-screen" id="main"><div class="empty-state"><div class="empty-icon">${icon('info')}</div><h2>Your library could not be opened</h2><p>${esc(error.message)}</p>${button('Try again', 'refresh', {glyph: 'rotation'})}</div></main>`;
  } finally {
    clearTimeout(refreshTimer);
    if (authenticated) refreshTimer = setTimeout(() => refresh({render: false, quiet: true}), running() ? 2500 : 15000);
  }
}
async function runAction(el, endpoint, body = {}, message = 'Working on it…', method = 'POST') {
  const original = el?.innerHTML;
  if (el) { el.disabled = true; el.setAttribute('aria-busy', 'true'); }
  try {
    const response = await api(endpoint, {method, body});
    if (message) toast(message);
    await refresh({render: page !== 'settings'});
    refreshDetailIfSafe();
    return response;
  } catch (error) { toast(error.message, true); return null; }
  finally { if (el?.isConnected) { el.disabled = false; el.removeAttribute('aria-busy'); if (original !== undefined) el.innerHTML = original; } }
}
function navigate(next) {
  if (!pages.includes(next)) return;
  if (page === next) return;
  location.hash = next;
}
async function saveSettings(form) {
  if (!form.reportValidity()) return false;
  const data = new FormData(form);
  const body = {advanced: {}};
  for (const [key, value] of data) {
    if (key.startsWith('advanced.')) continue;
    body[key] = value.trim();
  }
  form.querySelectorAll('[name^="advanced."]').forEach(input => { body.advanced[input.name.slice(9)] = input.checked; });
  await api('/api/settings', {method: 'POST', body});
  settingsDirty = false;
  form.querySelectorAll('input[type=password]').forEach(input => { if (input.value) input.placeholder = 'Saved — leave blank to keep'; input.value = ''; });
  document.querySelector('#settings-save-note').textContent = 'Settings saved. Credentials stay on your server.';
  return true;
}
document.addEventListener('click', async event => {
  const tab = event.target.closest('[data-tab]');
  if (tab) { detailTab = tab.dataset.tab; renderDetail(); detailDialog.querySelector(`[data-tab="${detailTab}"]`)?.focus(); return; }
  const chip = event.target.closest('[data-filter]');
  if (chip) { filter = chip.dataset.filter; renderPage(); document.querySelector(`[data-filter="${filter}"]`)?.focus(); return; }
  const el = event.target.closest('[data-action]');
  if (!el || el.disabled) return;
  event.preventDefault();
  const action = el.dataset.action;
  const id = el.dataset.id;
  if (['publish', 'apply-improvement', 'improve', 'request', 'request-all', 'add-available', 'describe', 'metadata'].includes(action) && hasCollectionEdits(id)) {
    toast('Save your collection edits before continuing.', true);
    detailTab = 'edit'; renderDetail();
    return;
  }
  if (pages.includes(action)) { navigate(action); return; }
  if (action === 'open') return openDetail(id);
  if (action === 'create') { createDialog.close(); return openCreate(); }
  if (action === 'import-dialog') return openImport();
  if (action === 'close-detail') return detailDialog.close();
  if (action === 'close-create') return createDialog.close();
  if (action === 'refresh') return refresh();
  if (action === 'drift-history') { filter = 'drift-history'; query = ''; if (page === 'collections') renderPage(); else navigate('collections'); return; }
  if (action === 'review-opportunity') { detailTab = 'edit'; renderDetail(); detailDialog.querySelector('[name="name"]')?.focus(); return; }
  if (action === 'discard-edits') { collectionEdits.delete(String(id)); renderDetail(); return; }
  if (action === 'sync') return runAction(el, '/api/library/sync', {}, 'Library sync started. You can keep browsing.');
  if (action === 'switch-drift') return runAction(el, '/api/drift/switch', {}, 'Switching shelves from the saved weekly pool.');
  if (action === 'generate') return runAction(el, '/api/drift/generate', {}, state.settings?.advanced?.auto_publish ? 'Generating a weekly pool. Reviewed choices are saved; only your Home slots publish now.' : 'Creating new Drift drafts for you to review.');
  if (action === 'rotate') return runAction(el, '/api/rotation/run', {}, 'Refreshing your permanent shelves on Plex Home.');
  if (action === 'legacy-import') return runAction(el, '/api/import', {}, 'Reading your previous collections.');
  if (action === 'publish') return runAction(el, `/api/collections/${encodeURIComponent(id)}/publish`, {}, 'Publishing this collection to Plex.');
  if (action === 'add-available') return runAction(el, `/api/collections/${encodeURIComponent(id)}/add`, {item_id: el.dataset.itemId}, getCollection(id)?.origin === 'drift' ? 'Reviewing this addition before it joins your Drift collection.' : 'Adding the library title to this collection.');
  if (action === 'family-refresh') return runAction(el, '/api/family-shelf/refresh', {}, 'Refreshing the newest family movies.');
  if (action === 'family-toggle') return runAction(el, '/api/family-shelf', {enabled:!state.family_shelf.enabled}, 'Updating family shelf automation.');
  if (action === 'arrival-review') return runAction(el, '/api/arrivals/review', {}, 'Reviewing new titles against your collections.');
  if (action === 'arrival-add' || action === 'arrival-dismiss') return runAction(el, `/api/arrivals/${encodeURIComponent(id)}/${action === 'arrival-add' ? 'add' : 'dismiss'}`, {}, action === 'arrival-add' ? 'Adding the reviewed title to its collection.' : 'Dismissing this suggestion.');
  if (action === 'collection-sweep') { if (!confirm('Review all permanent collections together? This uses one AI call and saves recommendations only. No titles will be moved, removed or requested.')) return; return runAction(el, '/api/collections/review', {}, 'Reviewing your permanent collections together.'); }
  if (action === 'request-progress') return runAction(el, '/api/requests/refresh', {}, 'Checking download and import progress.');
  if (action === 'metadata') return runAction(el, `/api/collections/${encodeURIComponent(id)}/metadata`, {}, 'Checking external titles against the catalog.');
  if (action === 'describe') return runAction(el, `/api/collections/${encodeURIComponent(id)}/describe`, {}, 'Refreshing the connection between these titles.');
  if (action === 'keep') return runAction(el, `/api/collections/${encodeURIComponent(id)}/keep`, {}, getCollection(id)?.status === 'published' ? 'Kept in your permanent rotation pool.' : 'Saved for your permanent pool. Publish the draft when it is ready.');
  if (action === 'adopt') return runAction(el, `/api/collections/${encodeURIComponent(id)}/adopt`, {}, 'Adding this collection to managed rotation. Its titles stay intact.');
  if (action === 'improve') { createDialog.close(); return openDiscovery(id); }
  if (action === 'discover-dialog') { createDialog.close(); return openDiscovery(); }
  if (action === 'apply-improvement') {
    const source = getCollection(getCollection(id)?.source_collection_id);
    const promotesDrift = source?.origin === 'drift' && !source.permanent;
    if (!confirm(promotesDrift ? 'Apply this edit and keep the collection permanently? This moves it from temporary Drift into your permanent rotation pool. Its titles and description will change; media files are kept.' : 'Apply this edit to the original Plex collection? Its membership and description will change. Media files are kept.')) return;
    return runAction(el, `/api/collections/${encodeURIComponent(id)}/apply`, {}, 'Applying the reviewed edit.');
  }
  if (action === 'save-opportunity') {
    const result = await runAction(el, `/api/drift/opportunities/${encodeURIComponent(id)}/save`, {}, 'Saved the idea for review.');
    if (result?.id) openDetail(result.id);
    return;
  }
  if (action === 'archive') {
    const c = getCollection(id);
    const response = await runAction(el, `/api/collections/${encodeURIComponent(id)}/archive`, {}, c?.status === 'published' ? 'Retired from rotation. The collection remains in Plex.' : 'Proposal dismissed. You can find it under Archived.');
    if (response && detailDialog.open) detailDialog.close();
    return;
  }
  if (action === 'request' || action === 'request-all') {
    const c = getCollection(id);
    const service = typeIcon(c?.media_type) === 'tv' ? 'Sonarr' : 'Radarr';
    const item = list(c?.missing)[Number(el.dataset.index)];
    const label = action === 'request-all' ? `all ${list(c?.missing).filter(requestable).length} missing titles` : `“${item?.title || item?.name || 'this title'}”`;
    if (!confirm(`Request ${label} in ${service}? This may start downloads using your ${service} settings.`)) return;
    return runAction(el, `/api/collections/${encodeURIComponent(id)}/request`, action === 'request-all' ? {all: true} : {index: Number(el.dataset.index)}, `Sending your request to ${service}.`);
  }
  if (action === 'test') {
    const form = document.querySelector('#settings-form');
    el.disabled = true;
    try { if (await saveSettings(form)) { await runAction(el, '/api/connections/test', {service: id}, `Testing ${id === 'llm' ? 'your naming model' : titleCase(id)}…`); await loadConnectionOptions(); } }
    catch (error) { toast(error.message, true); }
    finally { if (el.isConnected) el.disabled = false; }
    return;
  }
  if (action === 'logout') {
    try { await api('/api/logout', {method: 'POST', body: {}}); authenticated = false; state = null; csrf = ''; collectionEdits.clear(); rotationEdits = null; clearTimeout(refreshTimer); renderLogin(); }
    catch (error) { toast(error.message, true); }
  }
});
document.addEventListener('input', event => {
  const rotationForm = event.target.closest('#rotation-form');
  if (rotationForm) {
    const total = rotationForm.querySelector('[name=drift_slots]');
    const tv = rotationForm.querySelector('[name=drift_tv_slots]');
    tv.max = total.value;
    tv.closest('.field').querySelector('small').textContent = Number(tv.value) > Number(total.value) ? 'TV shelves cannot exceed the total Drift shelves.' : `${Math.max(0, Number(total.value) - Number(tv.value))} remaining Drift shelves are movies.`;
    rotationEdits = Object.fromEntries(new FormData(rotationForm));
    document.querySelector('#rotation-save-note').textContent = 'You have unsaved changes.';
  }
  const editForm = event.target.closest('#collection-edit-form');
  if (editForm) {
    const c = getCollection(editForm.dataset.id);
    const values = Object.fromEntries(new FormData(editForm));
    if (c && JSON.stringify(values) === JSON.stringify(savedCollectionFields(c))) collectionEdits.delete(String(c.id));
    else collectionEdits.set(String(editForm.dataset.id), values);
    updateCollectionEditControls();
  }
  if (event.target.id === 'collection-search') {
    query = event.target.value;
    document.querySelector('#collection-results').innerHTML = collectionResults();
  }
  if (event.target.closest('#settings-form')) {
    settingsDirty = true;
    document.querySelector('#settings-save-note').textContent = 'You have unsaved changes.';
  }
});
document.addEventListener('change', async event => {
  if (event.target.matches('[data-arrivals-toggle]')) {
    const input=event.target; const enabled=input.checked; input.disabled=true;
    try { await api('/api/settings', {method:'POST', body:{advanced:{new_arrival_suggestions:enabled}}}); await refresh(); toast(enabled ? 'New-arrival suggestions on.' : 'New-arrival suggestions paused.'); }
    catch(error) {input.checked=!enabled;toast(error.message,true);}
    finally {input.disabled=false;}
    return;
  }
  if (event.target.matches('[data-seasonal-toggle]')) {
    const input=event.target; const enabled=input.checked; input.disabled=true;
    try { await api('/api/settings', {method:'POST', body:{advanced:{seasonal_enabled:enabled}}}); await refresh(); toast(enabled ? 'Seasonal awareness on. Drift will use the current occasions.' : 'Seasonal awareness off.'); }
    catch(error) {input.checked=!enabled;toast(error.message,true);}
    finally {input.disabled=false;}
    return;
  }

  const input = event.target.closest('[data-rotation]');
  if (!input) return;
  const checked = input.checked;
  input.disabled = true;
  try { await api(`/api/collections/${encodeURIComponent(input.dataset.rotation)}`, {method: 'PATCH', body: {rotation_enabled: checked}}); await refresh(); toast(checked ? 'Collection added to the rotation pool.' : 'Collection removed from the rotation pool.'); }
  catch (error) { input.checked = !checked; toast(error.message, true); }
  finally { input.disabled = false; }
});
document.addEventListener('submit', async event => {
  const form = event.target;
  if (!['family-shelf-form', 'login-form', 'settings-form', 'rotation-form', 'create-form', 'collection-edit-form', 'trakt-form', 'discovery-form'].includes(form.id)) return;
  event.preventDefault();
  const submit = form.querySelector('[type=submit]');
  const errorId = {'family-shelf-form':'family-error', 'login-form': 'login-error', 'settings-form': 'settings-error', 'rotation-form': 'rotation-error', 'create-form': 'create-error', 'collection-edit-form': 'edit-error', 'trakt-form': 'trakt-error', 'discovery-form': 'discovery-error'}[form.id];
  const errorTarget = document.getElementById(errorId);
  if (errorTarget) errorTarget.textContent = '';
  submit.disabled = true;
  submit.setAttribute('aria-busy', 'true');
  try {
    const data = Object.fromEntries(new FormData(form));
    if (form.id === 'family-shelf-form') {
      await api('/api/family-shelf',{method:'POST',body:data});
      await refresh(); toast('Finding and pinning your newest family movies.');
    } else if (form.id === 'login-form') {
      const result = await api(accountSetupRequired ? '/api/setup' : '/api/login', {method: 'POST', body: data});
      accountSetupRequired = false;
      csrf = result.csrf || ''; authenticated = true; await refresh();
    } else if (form.id === 'settings-form') {
      if (await saveSettings(form)) { toast('Settings saved.'); await refresh({render: false}); await loadConnectionOptions(); }
    } else if (form.id === 'rotation-form') {
      const body = Object.fromEntries(Object.entries(data).map(([key, value]) => [key, Number(value)]));
      await api('/api/settings', {method: 'POST', body});
      if (!rotationEdits || JSON.stringify(rotationEdits) === JSON.stringify(data)) rotationEdits = null;
      await refresh();
      toast('Rotation setup saved. New counts and timings apply on the next refresh.');
    } else if (form.id === 'discovery-form') {
      const sourceId = form.dataset.sourceId;
      data.limit = Number(data.limit);
      data.auto_request = data.mode === 'expand' && form.querySelector('[name=auto_request]').checked;
      const endpoint = sourceId ? `/api/collections/${encodeURIComponent(sourceId)}/improve` : '/api/collections/discover';
      const result = await api(endpoint, {method:'POST',body:data});
      createDialog.close();
      if (sourceId) {
        improvementJobs.set(String(sourceId), result.id || result.job?.id);
        await refresh(); openDetail(sourceId);
        toast('Finding improvements. New suggestions will appear in this collection.');
      } else {
        detailDialog.close(); filter='draft'; navigate('collections'); await refresh(); toast('Curating your draft. Follow its progress above.');
      }
    } else if (form.id === 'create-form') {
      const autoRequest = form.querySelector('[name=auto_request]').checked;
      delete data.auto_request;
      const result = await api('/api/collections', {method: 'POST', body: data});
      let requestError = '';
      if (autoRequest && list(result.missing).length) {
        try { await api(`/api/collections/${encodeURIComponent(result.id)}/request`, {method:'POST',body:{all:true}}); }
        catch (error) { requestError = error.message; }
      }
      createDialog.close(); await refresh(); toast('Draft created. Review the matches before publishing.');
      const newId = result.id || result.collection?.id;
      if (newId) openDetail(newId); else navigate('collections');
      if (requestError) toast(`Your draft is saved, but missing titles could not be requested. ${requestError}`, true);
    } else if (form.id === 'collection-edit-form') {
      const body = {name: data.name, description: data.description, titles: data.titles};
      if (form.querySelector('[name=home]')) body.home = !!data.home;
      await api(`/api/collections/${encodeURIComponent(form.dataset.id)}`, {method: 'PATCH', body});
      // Text entered while the save was in flight remains an unsaved local edit.
      const pending = collectionEdits.get(String(form.dataset.id));
      if (!pending || JSON.stringify(pending) === JSON.stringify(body)) collectionEdits.delete(String(form.dataset.id));
      await refresh({render: page !== 'settings'}); toast('Collection updated.'); renderDetail();
    } else if (form.id === 'trakt-form') {
      await api('/api/import/trakt', {method: 'POST', body: data});
      createDialog.close(); await refresh(); toast('Importing your Trakt list.'); navigate('collections');
    }
  } catch (error) { if (errorTarget?.isConnected) errorTarget.textContent = error.message; else toast(error.message, true); }
  finally { if (submit.isConnected) { submit.disabled = false; submit.removeAttribute('aria-busy'); } }
});
document.addEventListener('keydown', event => {
  const tab = event.target.closest('[role=tab]');
  if (tab && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
    event.preventDefault();
    const tabs = ['titles', 'story', 'edit'];
    const current = tabs.indexOf(detailTab);
    detailTab = event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs[2] : tabs[(current + (event.key === 'ArrowRight' ? 1 : 2)) % 3];
    renderDetail(); detailDialog.querySelector(`[data-tab="${detailTab}"]`)?.focus();
  }
});
for (const dialog of [detailDialog, createDialog]) {
  dialog.addEventListener('click', event => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } });
}
window.addEventListener('hashchange', () => {
  const next = location.hash.slice(1);
  if (!pages.includes(next)) { location.hash = 'overview'; return; }
  if (page === 'settings' && settingsDirty) toast('Your unsaved settings were not applied.');
  page = next;
  detailDialog.close(); createDialog.close();
  renderPage();
  window.scrollTo({top: 0, behavior: 'instant'});
  document.querySelector('#main')?.focus({preventScroll: true});
});
window.addEventListener('beforeunload', event => { if (settingsDirty || rotationEdits || collectionEdits.size) { event.preventDefault(); event.returnValue = ''; } });
document.addEventListener('error', event => { if (event.target instanceof HTMLImageElement) event.target.style.visibility = 'hidden'; }, true);
async function boot() {
  try {
    const session = await api('/api/session');
    authenticated = !!session.authenticated; csrf = session.csrf || ''; accountSetupRequired = !!session.account_setup_required;
    if (!authenticated) renderLogin(); else await refresh();
  } catch (error) {
    app.innerHTML = `<main class="boot-screen" id="main"><div class="empty-state"><div class="empty-icon">${icon('info')}</div><h2>We couldn’t reach your server</h2><p>${esc(error.message)}</p><button class="button" id="retry-boot">Try again</button></div></main>`;
    document.querySelector('#retry-boot').addEventListener('click', boot);
  }
}
boot();

document.addEventListener('change', event => {
  if (event.target.matches('#discovery-form [name=mode]')) {
    const request = event.target.form.querySelector('[name=auto_request]');
    request.disabled = event.target.value !== 'expand';
    if (request.disabled) request.checked = false;
  }
});
