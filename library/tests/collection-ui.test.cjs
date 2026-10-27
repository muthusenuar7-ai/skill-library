// Exercise the shipped app.js and its real event listeners without a browser dependency.
// Run: node library/tests/collection-ui.test.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/app.js'), 'utf8');
const key = 'datacense.collection.v1';
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

function fixture(ids, {collection = true, failWrite = false, validate = false} = {}) {
  const listeners = {}, nodes = {}, requests = [];
  const saved = {[key]: JSON.stringify({v: 1, ids})};
  const element = () => ({dataset: {}, checked: false, disabled: false, hidden: false,
    textContent: '', innerHTML: '', handlers: {}, classList: {contains: () => false, toggle() {}},
    addEventListener(type, fn) { this.handlers[type] = fn; }});
  const boxes = ids.map(id => Object.assign(element(), {dataset: {collSelect: String(id)}}));
  nodes.toast = element();
  if (collection) {
    for (const id of ['coll-list', 'coll-all', 'coll-remove-selected', 'coll-sel-n', 'coll-clear',
      'coll-empty', 'coll-tools', 'coll-gone', 'coll-total', 'coll-total-n', 'coll-total-s', 'coll-download-selected']) nodes[id] = element();
    nodes['coll-list'].dataset.url = '/collection/items/';
    nodes['coll-list'].querySelectorAll = () => boxes;
  }
  const document = {
    getElementById: id => nodes[id] || null,
    querySelector: s => validate && s === 'meta[name=collection-check-url]' ? {content: '/collection/check/'} : null,
    querySelectorAll: s => s === '#coll-list .coll-check:checked' ? boxes.filter(b => b.checked)
      : s === '#coll-list .coll-check' ? boxes : [],
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
  };
  vm.runInNewContext(source, {document, window: {}, setTimeout() {}, clearTimeout() {},
    localStorage: {getItem: k => saved[k] || null, setItem(k, v) {if (failWrite) throw Error('quota'); saved[k] = v;}},
    fetch(url) {return new Promise(resolve => requests.push({url, resolve}));},
  });
  const dispatch = (type, target) => {
    const e = {target, prevented: false, preventDefault() {this.prevented = true;}};
    for (const fn of listeners[type] || []) fn(e);
    return e;
  };
  const submit = (checkedOnly = true) => {
    const input = {value: ''};
    const form = {dataset: {}, hasAttribute: a => a === 'data-collection-form' || checkedOnly && a === 'data-collection-checked',
      querySelector: () => input};
    return {event: dispatch('submit', form), ids: input.value};
  };
  const add = id => dispatch('change', {checked: true, dataset: {collect: String(id)}, closest() {return this;}});
  return {boxes, nodes, requests, submit, saved, add,
    async respond(data, index = 0) {requests[index].resolve({json: async () => data}); await flush();}};
}

test('Select All sends every ID for all required sizes, including nine', async () => {
  for (const count of [1, 2, 3, 4, 6, 8, 9, 10, 25]) {
    const ids = Array.from({length: count}, (_, i) => 100 + i);
    const f = fixture(ids);
    await f.respond({valid: ids, invalid: [], html: ''});
    assert.equal(f.nodes['coll-download-selected'].disabled, true);
    f.nodes['coll-all'].checked = true;
    f.nodes['coll-all'].handlers.change({target: f.nodes['coll-all']});
    assert.equal(f.nodes['coll-download-selected'].disabled, false);
    assert.deepEqual(f.submit().ids.split(',').map(Number), ids);
  }
});

test('Download Selected sends only checked rows; sticky download sends the collection', () => {
  const f = fixture([8, 3, 12, 5]);
  f.boxes[0].checked = f.boxes[3].checked = true;
  assert.equal(f.submit().ids, '8,5');
  assert.equal(f.submit(false).ids, '8,3,12,5');
});

test('empty checked selection prevents submission', () => {
  const f = fixture([8, 3]);
  const result = f.submit();
  assert.equal(result.event.prevented, true);
  assert.equal(result.ids, '');
});

test('failed localStorage writes use current in-memory selection rather than old saved IDs', () => {
  const f = fixture([1, 2], {collection: false, failWrite: true});
  f.add(3);
  assert.equal(f.submit(false).ids, '1,2,3');
});

test('late validity response preserves a skill added while validation was pending', async () => {
  const f = fixture([1, 2], {collection: false, validate: true});
  f.add(3);
  await f.respond({valid: [1]});
  assert.equal(f.submit(false).ids, '1,3');
});

test('late collection response reloads current IDs instead of painting stale rows', async () => {
  const f = fixture([1, 2]);
  f.add(3);
  await f.respond({valid: [1, 2], invalid: [], html: 'stale'});
  assert.equal(f.nodes['coll-list'].innerHTML, '');
  assert.equal(f.requests[1].url, '/collection/items/?ids=1,2,3');
});

test('repeated storage IDs submit once, retaining the last distinct ID', () => {
  const f = fixture([1, 1, 2, 3, 3], {collection: false});
  assert.equal(f.submit(false).ids, '1,2,3');
});
