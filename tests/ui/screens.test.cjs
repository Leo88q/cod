const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { JSDOM } = require('jsdom');

async function screen(hash, overrides = {}, uid = null) {
  const dom = new JSDOM(readFileSync('app/static/index.html', 'utf8'), {
    url: 'https://scena.test/app/#' + hash, runScripts: 'outside-only'
  });
  if (uid) dom.window.Telegram = {WebApp: {
    ready() {}, expand() {}, initData: 'dev-test-data', initDataUnsafe: {user: {id:uid}}
  }};
  const errors = [];
  dom.window.addEventListener('error', e => errors.push(e.message));
  dom.window.matchMedia = () => ({ matches: true });
  dom.window.fetch = async path => {
    const value = Object.hasOwn(overrides, path) ? overrides[path] : {
      scenes: [], cities: [], premium: false, user: null, lists: []
    };
    return { ok: true, json: async () => value };
  };
  dom.window.eval(readFileSync('app/static/app.js', 'utf8'));
  // Flush async handlers without running persistent timers.
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.deepEqual(errors, []);
  return dom;
}

test('create screen renders consent and fields without Telegram', async () => {
  const dom = await screen('/create');
  try {
    assert.ok(dom.window.document.querySelector('#f-recipient'));
    assert.match(dom.window.document.querySelector('#app').textContent, /Соглашаюсь/);
    assert.equal(dom.window.document.querySelector('#app').getAttribute('aria-busy'), 'false');
  } finally { dom.window.close(); }
});

test('locked gift does not render reply or media actions', async () => {
  const dom = await screen('/g/locked', {'/api/gifts/locked': {
    locked: true, open_at: new Date(Date.now() + 86400000).toISOString(),
    gift: {recipient_name: 'Аня', occasion: 'подарок'}
  }});
  try {
    assert.match(dom.window.document.querySelector('#app').textContent, /Откроется позже/);
    assert.equal(dom.window.document.querySelector('#gift-report'), null);
    assert.equal(dom.window.document.querySelector('audio'), null);
  } finally { dom.window.close(); }
});

test('timeline requires Telegram and does not leak data', async () => {
  const dom = await screen('/timeline');
  try { assert.match(dom.window.document.querySelector('#app').textContent, /из Telegram/); }
  finally { dom.window.close(); }
});

test('open gift renders scene, report and media refresh actions', async () => {
  const dom = await screen('/g/open', {'/api/gifts/open': {
    locked: false, gift: {recipient_name: 'Аня', view_count: 1, theme: 'cinema'},
    scenes: [{movie_title: 'Фильм', movie_year: 2000, timecode_label: '0:01:00', timecode_end_label: '0:01:30'}],
    replies: [], notes: []
  }});
  try {
    assert.ok(dom.window.document.querySelector('#gift-report'));
    assert.ok(dom.window.document.querySelector('#media-refresh'));
    assert.match(dom.window.document.querySelector('#app').textContent, /0:01:00–0:01:30/);
    assert.equal(dom.window.document.querySelector('canvas'), null);
  } finally { dom.window.close(); }
});

test('authenticated timeline exposes consent controls and account erasure', async () => {
  const dom = await screen('/timeline', {
    '/api/bookmarks?telegram_id=101': {scenes:[]},
    '/api/timeline/101': {total:0,peers:[],items:[]},
    '/api/connections?telegram_id=101': {connections:[{id:1,status:'pending',incoming:true,peer_telegram_id:102}]}
  }, 101);
  try {
    assert.ok(dom.window.document.querySelector('.connection-accept'));
    assert.ok(dom.window.document.querySelector('.connection-remove'));
    assert.match(dom.window.document.querySelector('#app').textContent, /Удалить профиль/);
  } finally { dom.window.close(); }
});

test('studio picker only renders assigned studios', async () => {
  const dom = await screen('/shop', {
    '/api/bookmarks?telegram_id=101': {scenes:[]},
    '/api/shops/mine?telegram_id=101': {shops:[{id:8,name:'Моя студия',city:'Москва'}]}
  }, 101);
  try {
    const button = dom.window.document.querySelector('.studio-open');
    assert.equal(button.dataset.id, '8');
    assert.match(button.textContent, /Моя студия/);
  } finally { dom.window.close(); }
});

test('admin operational view renders failed notifications, audit and timecode queue', async () => {
  const dom = await screen('/admin', {
    '/api/admin/overview': {users:1,scenes:1,gifts:0,shops:1,cards:0,revenue_rub:0},
    '/api/admin/reports': {reports:[]},
    '/api/admin/metrics': {worker:{status:'degraded',age_seconds:10},pending_notifications:1,failed_notifications:1},
    '/api/admin/notifications/failed': {notifications:[{id:2,kind:'reply',attempts:8}]},
    '/api/admin/audit': {entries:[{created_at:'2026-11-04',action:'notification_retry',actor:'admin-token',resource:'2'}]},
    '/api/admin/analytics': {funnel:{}},
    '/api/admin/scenes?status=draft': {scenes:[]},
    '/api/admin/scenes?status=approved&verified=0': {scenes:[]},
    '/api/admin/timecodes?status=pending': {candidates:[]}
  });
  try {
    dom.window.document.querySelector('#adm-load').click();
    await new Promise(resolve => setTimeout(resolve, 40));
    assert.ok(dom.window.document.querySelector('.notification-retry'));
    assert.match(dom.window.document.querySelector('#adm-body').textContent, /notification_retry/);
    assert.ok(dom.window.document.querySelector('#tc-bulk'));
  } finally { dom.window.close(); }
});
