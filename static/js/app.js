/* Datacense Skill Library - small progressive-enhancement helpers. No framework, no inline code (CSP-safe).
   Everything essential (browsing, search, filtering, authorization, ZIP building) happens on the server. */
(function () {
  'use strict';
  var FAV_KEY = 'datacense.favorites';            // saved skills (slugs)
  var COLL_KEY = 'datacense.collection.v1';       // My Collection: {v:1, ids:[numeric skill ids]} - ids only, never content
  var metaLimit = document.querySelector('meta[name=collection-limit]');
  var LIMIT = metaLimit ? parseInt(metaLimit.content, 10) || 100 : 100;

  /* ---------- small helpers ---------- */
  var toastTimer;
  function toast(msg, kind) {
    var el = document.getElementById('toast');
    if (!el) return;
    el.textContent = msg; el.className = 'toast' + (kind ? ' ' + kind : ''); el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { el.hidden = true; }, 4200);
  }
  var dlg = document.getElementById('confirm-dialog');
  function askConfirm(message, proceed, okLabel) {
    if (!dlg || typeof dlg.showModal !== 'function') { if (window.confirm(message)) proceed(); return; }
    document.getElementById('confirm-message').textContent = message;
    document.getElementById('confirm-ok').textContent = okLabel || 'Confirm';
    dlg.returnValue = '';
    dlg.onclose = function () { if (dlg.returnValue === 'ok') proceed(); };
    dlg.showModal();  // native modal: focus moves in, is trapped, and returns to the trigger on close
  }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }
  function uniq(list) { return list.filter(function (x, i) { return list.indexOf(x) === i; }); }

  /* ---------- storage with graceful fallback ---------- */
  var storageWarned = false, memory = {}, storageFailed = {};
  function warnStorage() {
    if (storageWarned) return; storageWarned = true;
    toast('Your browser is blocking saved selections. They will be lost when you leave this page.', 'error');
  }
  function getItem(key) { if (storageFailed[key]) return memory[key] || null; try { return localStorage.getItem(key); } catch (e) { warnStorage(); return memory[key] || null; } }
  function setItem(key, value) { memory[key] = value; try { localStorage.setItem(key, value); } catch (e) { storageFailed[key] = true; warnStorage(); } }

  /* ---------- favorites (slugs) ---------- */
  function readFavs() {
    try { var v = JSON.parse(getItem(FAV_KEY) || '[]'); return Array.isArray(v) ? uniq(v.filter(function (s) { return typeof s === 'string'; })) : []; } catch (e) { return []; }
  }
  function writeFavs(list) { setItem(FAV_KEY, JSON.stringify(uniq(list))); }

  /* ---------- My Collection (numeric ids) - independent from favorites ---------- */
  function readColl() {
    try {
      var v = JSON.parse(getItem(COLL_KEY) || 'null');
      if (v && v.v === 1 && Array.isArray(v.ids)) return uniq(v.ids.map(Number).filter(function (n) { return n > 0 && isFinite(n); }));
    } catch (e) { /* corrupt value: start empty */ }
    return [];
  }
  function writeColl(ids) { setItem(COLL_KEY, JSON.stringify({ v: 1, ids: uniq(ids) })); paintColl(); }
  function collAdd(ids) {
    var cur = readColl(), added = 0, skipped = 0;
    uniq(ids.map(Number)).forEach(function (id) {
      if (cur.indexOf(id) !== -1) return;
      if (cur.length >= LIMIT) { skipped++; return; }
      cur.push(id); added++;
    });
    writeColl(cur);
    if (skipped) toast('My Collection holds up to ' + LIMIT + ' skills. ' + plural(skipped, 'skill was', 'skills were') + ' not added.', 'error');
    return added;
  }
  function collRemove(ids) { var drop = ids.map(Number); writeColl(readColl().filter(function (i) { return drop.indexOf(i) === -1; })); }

  function paintFavs() {
    var favs = readFavs();
    document.querySelectorAll('[data-fav]').forEach(function (btn) {
      var on = favs.indexOf(btn.dataset.fav) !== -1;
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
      var label = btn.getAttribute('aria-label');
      if (btn.classList.contains('fav')) {
        btn.textContent = on ? '★' : '☆';
        if (label) btn.setAttribute('aria-label', label.replace(/^(Save|Remove)(.*?)(to|from) My Favorites$/, function (m, a, mid) { return (on ? 'Remove' : 'Save') + mid + (on ? 'from' : 'to') + ' My Favorites'; }));
      } else btn.textContent = on ? '★ In Favorites' : '☆ Add to Favorites';
    });
    document.querySelectorAll('[data-fav-count]').forEach(function (el) { el.textContent = favs.length; });
  }

  function paintColl() {
    var ids = readColl(), n = ids.length;
    document.querySelectorAll('[data-collect]').forEach(function (box) {
      var on = ids.indexOf(Number(box.dataset.collect)) !== -1;
      box.checked = on;
      var label = box.closest('.collect');
      if (label) { label.classList.toggle('is-on', on); label.querySelector('.ct').textContent = on ? 'In Collection' : 'Add to Collection'; }
    });
    document.querySelectorAll('[data-coll-count]').forEach(function (el) { el.textContent = n; });
    var bar = document.getElementById('collection-bar');
    if (bar) {
      bar.hidden = n === 0;
      document.body.classList.toggle('has-bar', n > 0);
      document.getElementById('cb-count').textContent = n;
      document.getElementById('cb-label').textContent = n === 1 ? 'skill selected' : 'skills selected';
    }
  }

  /* ---------- keep the collection honest: drop skills that were unpublished or removed ---------- */
  function validateColl() {
    var ids = readColl();
    if (!ids.length || document.getElementById('coll-list')) return;
    var checkMeta = document.querySelector('meta[name=collection-check-url]');
    if (!checkMeta) return;
    fetch(checkMeta.content + '?ids=' + ids.join(','), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var invalid = ids.filter(function (id) { return d.valid.indexOf(id) === -1; });
        if (invalid.length) {
          collRemove(invalid); // Keep skills added while this check was in flight.
          toast(plural(invalid.length, 'skill is', 'skills are') + ' no longer available and was removed from My Collection.', 'error');
        }
      })
      .catch(function () { /* offline: keep what we have */ });
  }

  /* ---------- Favorites page ---------- */
  function favPageSelected() { return Array.prototype.map.call(document.querySelectorAll('#fav-list .fav-check:checked'), function (c) { return c.dataset.favSelect; }); }
  function updateFavTools() {
    var sel = favPageSelected().length, rm = document.getElementById('fav-remove-selected');
    if (!rm) return;
    document.getElementById('fav-sel-n').textContent = sel;
    rm.disabled = sel === 0; document.getElementById('fav-add-selected').disabled = sel === 0;
    var all = document.getElementById('fav-all'), boxes = document.querySelectorAll('#fav-list .fav-check');
    all.checked = boxes.length > 0 && sel === boxes.length;
  }
  function loadFavoritesPage() {
    var list = document.getElementById('fav-list');
    if (!list) return;
    var favs = readFavs(), empty = document.getElementById('fav-empty'), tools = document.getElementById('fav-tools'), missingBox = document.getElementById('fav-missing');
    if (!favs.length) { list.innerHTML = ''; empty.hidden = false; tools.hidden = true; missingBox.hidden = true; return; }
    fetch(list.dataset.url + '?slugs=' + encodeURIComponent(favs.join(',')), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        list.innerHTML = data.html;
        empty.hidden = data.found.length > 0; tools.hidden = data.found.length === 0;
        var missing = favs.filter(function (s) { return data.found.indexOf(s) === -1; });
        missingBox.hidden = missing.length === 0;
        if (missing.length) {
          document.getElementById('fav-missing-text').textContent = plural(missing.length, 'saved skill is', 'saved skills are') + ' no longer available.';
          document.getElementById('fav-prune').onclick = function () { writeFavs(readFavs().filter(function (s) { return missing.indexOf(s) === -1; })); paintFavs(); loadFavoritesPage(); };
        }
        paintFavs(); paintColl(); updateFavTools();
      })
      .catch(function () { list.innerHTML = '<li class="empty-note">Your favorites could not be loaded. Please try again.</li>'; });
  }
  function favIdsFor(slugs) {
    return slugs.map(function (s) { var li = document.querySelector('#fav-list > li[data-fav-card="' + s + '"]'); return li ? Number(li.dataset.id) : 0; }).filter(Boolean);
  }
  function wireFavoritesPage() {
    var all = document.getElementById('fav-all');
    if (!all) return;
    all.addEventListener('change', function () { document.querySelectorAll('#fav-list .fav-check').forEach(function (c) { c.checked = all.checked; }); updateFavTools(); });
    document.getElementById('fav-list').addEventListener('change', function (e) { if (e.target.classList.contains('fav-check')) updateFavTools(); });
    document.getElementById('fav-remove-selected').addEventListener('click', function () {
      var sel = favPageSelected();
      if (!sel.length) return;
      writeFavs(readFavs().filter(function (s) { return sel.indexOf(s) === -1; }));
      sel.forEach(function (s) { var li = document.querySelector('#fav-list > li[data-fav-card="' + s + '"]'); if (li) li.remove(); });
      paintFavs(); updateFavTools();
      toast(plural(sel.length, 'skill was', 'skills were') + ' removed from My Favorites.', 'ok');
      if (!document.querySelector('#fav-list > li')) { document.getElementById('fav-empty').hidden = false; document.getElementById('fav-tools').hidden = true; }
    });
    document.getElementById('fav-add-selected').addEventListener('click', function () {
      var n = collAdd(favIdsFor(favPageSelected())); toast(plural(n, 'skill was', 'skills were') + ' added to My Collection.', 'ok');
    });
    document.getElementById('fav-add-all').addEventListener('click', function () {
      var n = collAdd(favIdsFor(readFavs())); toast(plural(n, 'skill was', 'skills were') + ' added to My Collection.', 'ok');
    });
    document.getElementById('fav-clear').addEventListener('click', function () {
      var n = readFavs().length;
      askConfirm('Remove all ' + plural(n, 'skill', 'skills') + ' from your Favorites?', function () {
        writeFavs([]); paintFavs(); loadFavoritesPage(); toast('Your Favorites were cleared. My Collection was not changed.', 'ok');
      }, 'Clear All Favorites');
    });
  }

  /* ---------- My Collection page ---------- */
  function collPageSelected() { return Array.prototype.map.call(document.querySelectorAll('#coll-list .coll-check:checked'), function (c) { return Number(c.dataset.collSelect); }); }
  function updateCollTools() {
    var sel = collPageSelected().length, rm = document.getElementById('coll-remove-selected');
    if (!rm) return;
    document.getElementById('coll-sel-n').textContent = sel; rm.disabled = sel === 0;
    document.getElementById('coll-download-selected').disabled = sel === 0;
    var boxes = document.querySelectorAll('#coll-list .coll-check'), all = document.getElementById('coll-all');
    all.checked = boxes.length > 0 && sel === boxes.length;
    all.indeterminate = sel > 0 && sel < boxes.length;
  }
  var collectionLoad = 0;
  function loadCollectionPage() {
    var box = document.getElementById('coll-list');
    if (!box) return;
    var load = ++collectionLoad;
    var ids = readColl(), empty = document.getElementById('coll-empty'), tools = document.getElementById('coll-tools'), gone = document.getElementById('coll-gone'), total = document.getElementById('coll-total');
    document.getElementById('coll-download-selected').disabled = true;
    if (!ids.length) { box.innerHTML = ''; empty.hidden = false; tools.hidden = true; total.hidden = true; return; }
    fetch(box.dataset.url + '?ids=' + ids.join(','), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (load !== collectionLoad) return;
        if (readColl().join(',') !== ids.join(',')) { loadCollectionPage(); return; }
        if (d.invalid.length) {
          gone.hidden = false; gone.textContent = plural(d.invalid.length, 'skill is', 'skills are') + ' no longer available (unpublished or removed). ' + (d.invalid.length === 1 ? 'It was' : 'They were') + ' taken out of your collection and will not be downloaded.';
          writeColl(d.valid);
        } else gone.hidden = true;
        box.innerHTML = d.html;
        empty.hidden = d.valid.length > 0; tools.hidden = d.valid.length === 0; total.hidden = d.valid.length === 0;
        document.getElementById('coll-total-n').textContent = d.valid.length; document.getElementById('coll-total-s').textContent = d.valid.length === 1 ? '' : 's';
        updateCollTools();
      })
      .catch(function () { if (load === collectionLoad) { box.innerHTML = '<p class="empty-note">Your collection could not be loaded. Please try again.</p>'; updateCollTools(); } });
  }
  function wireCollectionPage() {
    var box = document.getElementById('coll-list');
    if (!box) return;
    document.getElementById('coll-all').addEventListener('change', function (e) { box.querySelectorAll('.coll-check').forEach(function (c) { c.checked = e.target.checked; }); updateCollTools(); });
    box.addEventListener('change', function (e) { if (e.target.classList.contains('coll-check')) updateCollTools(); });
    box.addEventListener('click', function (e) { var b = e.target.closest('[data-coll-remove]'); if (b) { collRemove([b.dataset.collRemove]); loadCollectionPage(); toast('Removed from My Collection.', 'ok'); } });
    document.getElementById('coll-remove-selected').addEventListener('click', function () { var sel = collPageSelected(); collRemove(sel); loadCollectionPage(); toast(plural(sel.length, 'skill was', 'skills were') + ' removed from My Collection.', 'ok'); });
    document.getElementById('coll-clear').addEventListener('click', function () { clearCollection(function () { loadCollectionPage(); }); });
  }
  function clearCollection(after) {
    var n = readColl().length;
    if (!n) return;
    askConfirm('Remove all ' + plural(n, 'skill', 'skills') + ' from My Collection? Your Favorites are not affected.', function () {
      writeColl([]); toast('My Collection was cleared. Your Favorites were not changed.', 'ok'); if (after) after();
    }, 'Clear Collection');
  }

  /* ---------- Explore: add-to-collection checkboxes, page/matching selection ---------- */
  function wireSelectBar() {
    var bar = document.getElementById('selectbar');
    if (!bar) return;
    bar.hidden = false;
    var pageIds = function () { return Array.prototype.map.call(document.querySelectorAll('.cards [data-collect]'), function (b) { return Number(b.dataset.collect); }); };
    document.getElementById('sel-page').addEventListener('click', function () { var n = collAdd(pageIds()); toast(n ? plural(n, 'skill was', 'skills were') + ' added from this page.' : 'Everything on this page is already in My Collection.', 'ok'); });
    document.getElementById('sel-page-none').addEventListener('click', function () { collRemove(pageIds()); toast('Skills on this page were removed from My Collection.', 'ok'); });
    var match = document.getElementById('sel-match');
    if (match) match.addEventListener('click', function () {
      fetch(bar.dataset.matchUrl, { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
        if (!d.count) { toast('Nothing matches the current search and filters.', 'error'); return; }
        var room = Math.max(0, d.limit - readColl().length), will = Math.min(d.ids.length, d.limit);
        var msg = 'Add all ' + plural(d.count, 'skill', 'skills') + ' that match your current search and filters to My Collection?';
        if (d.count > d.limit) msg = plural(d.count, 'skill matches', 'skills match') + ' your search, but My Collection holds up to ' + d.limit + '. Add the first ' + will + ' (up to the space left, ' + room + ')? Narrow your filters to choose precisely.';
        askConfirm(msg, function () { var n = collAdd(d.ids); toast(plural(n, 'skill was', 'skills were') + ' added to My Collection.', 'ok'); }, 'Add to Collection');
      }).catch(function () { toast('Could not check the matching skills. Please try again.', 'error'); });
    });
  }

  /* ---------- categories page: live filter ---------- */
  function wireCategoryFilter() {
    var wrap = document.getElementById('cat-filter-form'), input = document.getElementById('cat-filter');
    if (!wrap || !input) return;
    wrap.hidden = false;
    var clear = document.getElementById('cat-filter-clear'), status = document.getElementById('cat-filter-status'), empty = document.getElementById('cat-empty');
    var apply = function () {
      var q = input.value.trim().toLowerCase(), shown = 0;
      document.querySelectorAll('#cat-grid .cat-card').forEach(function (card) {
        var domainHit = !q || card.dataset.name.indexOf(q) !== -1, anySub = false;
        card.querySelectorAll('.sub-rows li').forEach(function (li) {
          var hit = !q || domainHit || li.dataset.name.indexOf(q) !== -1;  // a matching domain keeps all its subcategories
          li.hidden = !hit; if (hit && q && li.dataset.name.indexOf(q) !== -1) anySub = true;
        });
        var visible = domainHit || anySub;  // a parent domain stays when one of its subcategories matches
        card.hidden = !visible; if (visible) shown++;
      });
      clear.hidden = !q; empty.hidden = shown > 0;
      status.textContent = q ? (shown ? plural(shown, 'domain matches', 'domains match') + ' “' + input.value.trim() + '”.' : '') : '';
    };
    input.addEventListener('input', apply);
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') e.preventDefault(); if (e.key === 'Escape') { input.value = ''; apply(); } });
    [clear, document.getElementById('cat-empty-clear')].forEach(function (b) { b.addEventListener('click', function () { input.value = ''; apply(); input.focus(); }); });
  }

  /* ---------- global click handling ---------- */
  document.addEventListener('click', function (e) {
    var t = e.target.closest('button, a');
    if (!t) return;
    if (t.dataset.fav) {
      var favs = readFavs(), i = favs.indexOf(t.dataset.fav), removing = i !== -1;
      if (removing) favs.splice(i, 1); else favs.push(t.dataset.fav);
      writeFavs(favs); paintFavs();
      var item = t.closest('#fav-list > li');
      if (item && removing) { item.remove(); updateFavTools(); if (!document.querySelector('#fav-list > li')) { document.getElementById('fav-empty').hidden = false; document.getElementById('fav-tools').hidden = true; } }
    }
    if (t.dataset.copyTarget) {
      var src = document.getElementById(t.dataset.copyTarget), text = src ? src.textContent : '';
      var done = function () { var old = t.textContent; t.textContent = 'Copied ✓'; setTimeout(function () { t.textContent = old; }, 1800); };
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text, done); }); else fallbackCopy(text, done);
    }
    if (t.classList.contains('nav-toggle')) { var nav = document.getElementById('main-nav'); t.setAttribute('aria-expanded', nav.classList.toggle('open') ? 'true' : 'false'); }
    if (t.id === 'console-menu') { var side = document.getElementById('console-side'); t.setAttribute('aria-expanded', side.classList.toggle('open') ? 'true' : 'false'); }
    if (t.id === 'cb-clear') clearCollection();
  });
  document.addEventListener('change', function (e) {
    var box = e.target.closest('[data-collect]');
    if (!box) return;
    var id = Number(box.dataset.collect);
    if (box.checked) { if (!collAdd([id])) paintColl(); } else collRemove([id]);
  });
  function fallbackCopy(text, done) {
    var ta = document.createElement('textarea'); ta.value = text; ta.setAttribute('readonly', ''); ta.className = 'sr-only';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch (err) { window.prompt('Copy the text below:', text); }
    document.body.removeChild(ta);
  }

  /* ZIP forms: the ids/slugs are filled in from this browser just before the (CSRF-protected) POST */
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (form.hasAttribute('data-collection-form')) {
      var checkedOnly = form.hasAttribute('data-collection-checked');
      var ids = uniq(checkedOnly ? collPageSelected() : readColl());
      if (!ids.length) { e.preventDefault(); toast(checkedOnly ? 'Select at least one skill to download.' : 'Your collection is empty.', 'error'); return; }
      form.querySelector('input[name=ids]').value = ids.join(',');
      toast('Preparing your ZIP...', 'ok');
    } else if (form.hasAttribute('data-favorites-form')) {
      var slugs = readFavs();
      if (!slugs.length) { e.preventDefault(); toast('You have no favorites to download.', 'error'); return; }
      form.querySelector('input[name=slugs]').value = slugs.join(',');
      toast('Preparing your ZIP...', 'ok');
    }
  });

  /* ---------- Explore: mobile filter panel (focus managed, Esc closes) ---------- */
  var panel = document.getElementById('filters-panel');
  if (panel) {
    var openBtn = document.getElementById('filters-open'), closeBtn = document.getElementById('filters-close'), backdrop = document.getElementById('filters-backdrop');
    var mobile = window.matchMedia('(max-width: 860px)');
    var setOpen = function (open) {
      panel.classList.toggle('open', open); backdrop.classList.toggle('open', open);
      openBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (mobile.matches) {
        panel.setAttribute('role', open ? 'dialog' : '');
        if (open) { panel.setAttribute('aria-modal', 'true'); closeBtn.focus(); } else { panel.removeAttribute('aria-modal'); openBtn.focus(); }
      }
    };
    openBtn.addEventListener('click', function () { setOpen(true); });
    closeBtn.addEventListener('click', function () { setOpen(false); });
    backdrop.addEventListener('click', function () { setOpen(false); });
    document.addEventListener('keydown', function (e) {
      if (!panel.classList.contains('open')) return;
      if (e.key === 'Escape') { setOpen(false); return; }
      if (e.key === 'Tab') {
        var f = panel.querySelectorAll('a[href], button, input, summary');
        if (!f.length) return;
        var first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });
    var wrap = document.getElementById('cat-search-wrap'), box = document.getElementById('cat-search'), none = document.getElementById('cat-none');
    wrap.hidden = false;
    var tools = document.getElementById('tree-tools'); tools.hidden = false;
    tools.addEventListener('click', function (e) { var b = e.target.closest('[data-tree]'); if (!b) return; document.querySelectorAll('#tree details').forEach(function (d) { d.open = b.dataset.tree === 'open'; }); });
    box.addEventListener('input', function () {
      var q = box.value.trim().toLowerCase(), shown = 0;
      document.querySelectorAll('#tree > li.dom').forEach(function (li) {
        var hit = !q || li.dataset.search.indexOf(q) !== -1; li.hidden = !hit; if (hit) shown++;
        var d = li.querySelector('details'); if (q && hit) d.open = true;
      });
      none.hidden = shown > 0 || !q;
    });
  }
  document.querySelectorAll('[data-autosubmit]').forEach(function (el) { el.addEventListener('change', function () { el.form.submit(); }); });

  /* ---------- management: confirmation dialog for data-confirm ---------- */
  document.addEventListener('submit', function (e) {
    var form = e.target, btn = e.submitter;
    var msg = (btn && btn.dataset && btn.dataset.confirm) || (form.dataset && form.dataset.confirm);
    if (form.dataset && form.dataset.confirmed) return;
    if (btn && btn.hasAttribute('data-bulk-apply')) {
      var sel = form.querySelector('#bulk-action'), n = form.querySelectorAll('.row-check:checked').length;
      if (!sel.value || !n) { e.preventDefault(); window.alert(!sel.value ? 'Choose a bulk action first.' : 'Tick at least one skill first.'); return; }
      msg = 'Apply "' + sel.options[sel.selectedIndex].text + '" to ' + n + ' selected skill' + (n === 1 ? '' : 's') + '?';
    }
    if (!msg) return;
    e.preventDefault();
    askConfirm(msg, function () {
      form.dataset.confirmed = '1';
      if (btn && btn.name) { var h = document.createElement('input'); h.type = 'hidden'; h.name = btn.name; h.value = btn.value; form.appendChild(h); }
      if (btn && btn.formAction) form.action = btn.formAction;
      form.submit();
    });
  });

  var all = document.getElementById('check-all'), counter = document.getElementById('bulk-count');
  function updateCount() { if (counter) counter.textContent = document.querySelectorAll('.row-check:checked').length; }
  if (all) all.addEventListener('change', function () { document.querySelectorAll('.row-check').forEach(function (c) { c.checked = all.checked; }); updateCount(); });
  document.querySelectorAll('.row-check').forEach(function (c) { c.addEventListener('change', updateCount); });

  var title = document.getElementById('id_title') || document.getElementById('id_name'), slug = document.getElementById('id_slug');
  if (title && slug) {
    var touched = slug.value !== '';
    slug.addEventListener('input', function () { touched = true; });
    title.addEventListener('input', function () {
      if (touched) return;
      slug.value = title.value.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 80);
    });
  }
  var domainSel = document.getElementById('id_category'), subSel = document.getElementById('id_subcategory');
  if (domainSel && subSel) {
    var syncSubs = function () {
      var d = domainSel.value, keep = false;
      Array.prototype.forEach.call(subSel.options, function (o) { var ok = !o.value || o.dataset.parent === d; o.hidden = !ok; o.disabled = !ok; if (ok && o.selected) keep = true; });
      if (!keep) subSel.value = '';
      subSel.disabled = !d;
    };
    domainSel.addEventListener('change', syncSubs); syncSubs();
  }
  var zone = document.getElementById('dropzone'), input = document.getElementById('import-files');
  if (zone && input) {
    var names = document.getElementById('file-names');
    var show = function () { names.textContent = Array.prototype.map.call(input.files, function (f) { return f.name; }).join(', '); };
    ['dragenter', 'dragover'].forEach(function (ev) { zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add('over'); }); });
    ['dragleave', 'drop'].forEach(function (ev) { zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.remove('over'); }); });
    zone.addEventListener('drop', function (e) { if (e.dataTransfer && e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); } });
    input.addEventListener('change', show);
    var form = document.getElementById('import-form');
    form.addEventListener('submit', function (e) {
      if (!input.files.length || !window.XMLHttpRequest || !window.FormData) return;
      e.preventDefault();
      var box = document.getElementById('upload-progress'), bar = document.getElementById('upload-bar'), label = document.getElementById('upload-text'), btn = document.getElementById('import-submit');
      box.hidden = false; btn.disabled = true;
      var xhr = new XMLHttpRequest();
      xhr.open('POST', form.action || window.location.href);
      xhr.upload.onprogress = function (ev) { if (ev.lengthComputable) { bar.value = Math.round(ev.loaded / ev.total * 100); label.textContent = 'Uploading... ' + bar.value + '%'; } };
      xhr.upload.onload = function () { bar.removeAttribute('value'); label.textContent = 'Analysing files...'; };
      xhr.onload = function () { if (xhr.status === 200 && /\/import\/review\//.test(xhr.responseURL)) window.location.assign(xhr.responseURL); else window.location.assign(window.location.href); };
      xhr.onerror = function () { label.textContent = 'Upload failed. Check your connection and try again.'; btn.disabled = false; };
      xhr.send(new FormData(form));
    });
  }
  var skipDups = document.getElementById('skip-dups');
  if (skipDups) skipDups.addEventListener('click', function () { document.querySelectorAll('select[data-dup="1"]').forEach(function (s) { s.value = 'skip'; }); });
  var mdTabs = document.querySelectorAll('[data-md-tab]');
  if (mdTabs.length) {
    var ta = document.getElementById('id_content'), pane = document.getElementById('md-preview'), csrf = document.querySelector('input[name=csrfmiddlewaretoken]');
    mdTabs.forEach(function (btn) {
      btn.addEventListener('click', function () {
        var preview = btn.dataset.mdTab === 'preview';
        mdTabs.forEach(function (b) { b.setAttribute('aria-selected', b === btn ? 'true' : 'false'); });
        ta.hidden = preview; pane.hidden = !preview;
        if (!preview) return;
        pane.textContent = 'Rendering...';
        var fd = new FormData(); fd.append('content', ta.value); if (csrf) fd.append('csrfmiddlewaretoken', csrf.value);
        fetch(btn.dataset.previewUrl, { method: 'POST', body: fd, credentials: 'same-origin' }).then(function (r) { return r.json(); })
          .then(function (j) { pane.innerHTML = j.html || '<p class="muted">Nothing to preview yet.</p>'; })
          .catch(function () { pane.textContent = 'Preview is not available right now.'; });
      });
    });
  }

  /* ---------- management: Activity - clear (archive) with a count-confirming dialog ---------- */
  var clearForm = document.getElementById('clear-form');
  if (clearForm) {
    var adlg = document.getElementById('activity-dialog'), allBox = document.getElementById('act-select-all'), selN = document.getElementById('act-sel-n');
    var checks = function () { return Array.prototype.slice.call(document.querySelectorAll('.act-check')); };
    var updateSel = function () { var n = checks().filter(function (c) { return c.checked; }).length; selN.textContent = n; allBox.checked = n > 0 && n === checks().length; };
    allBox.addEventListener('change', function () { checks().forEach(function (c) { c.checked = allBox.checked; }); updateSel(); });
    document.addEventListener('change', function (e) { if (e.target.classList && e.target.classList.contains('act-check')) updateSel(); });
    var confirmClear = function (mode, count, kept, before) {
      document.getElementById('ad-text').textContent = 'Are you sure you want to clear ' + plural(count, 'activity record', 'activity records') + '? This action cannot be undone.';
      document.getElementById('ad-kept').textContent = (kept ? plural(kept, 'protected security record', 'protected security records') + ' in this selection will be kept. ' : '') + 'The clearing itself is recorded in the security audit trail.';
      document.getElementById('ad-ok').textContent = 'Clear ' + plural(count, 'record', 'records');
      adlg.returnValue = '';
      adlg.onclose = function () {
        if (adlg.returnValue !== 'ok') return;
        var f = clearForm.elements;
        f.mode.value = mode; f.before.value = before || ''; f.expected_count.value = count; f.confirm.value = 'yes';
        clearForm.submit();
      };
      adlg.showModal();
    };
    document.querySelectorAll('[data-clear-mode]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var mode = btn.dataset.clearMode, before = '';
        if (mode === 'selected') {
          var n = checks().filter(function (c) { return c.checked; }).length;
          if (!n) { toast('Tick at least one activity record first.', 'error'); return; }
          confirmClear('selected', n, 0, ''); return;
        }
        if (mode === 'older') {
          before = document.getElementById('act-before').value;
          if (!before) { toast('Choose a date first: records older than it will be cleared.', 'error'); document.getElementById('act-before').focus(); return; }
        }
        fetch(clearForm.dataset.countUrl + '?mode=' + mode + (before ? '&before=' + encodeURIComponent(before) : ''), { credentials: 'same-origin' })
          .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
          .then(function (res) {
            if (!res.ok) { toast(res.d.error || 'Could not check the records.', 'error'); return; }
            if (!res.d.count) { toast('There are no clearable records for that choice.', 'error'); return; }
            confirmClear(mode, res.d.count, res.d.protected_kept, before);
          })
          .catch(function () { toast('Could not check the records. Please try again.', 'error'); });
      });
    });
  }

  /* ---------- start ---------- */
  paintFavs(); paintColl();
  wireFavoritesPage(); wireCollectionPage(); wireSelectBar(); wireCategoryFilter();
  loadFavoritesPage(); loadCollectionPage(); validateColl();
})();
