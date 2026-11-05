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

test('landing escapes untrusted approved scene fields', async () => {
  const dom = new JSDOM(readFileSync('app/static/landing.html', 'utf8'), {
    url:'https://scena.test/', runScripts:'outside-only'
  });
  try {
    dom.window.fetch = async () => ({json: async () => ({movie_title:'<img src=x onerror=alert(1)>', movie_year:2000,
      description:'<script>evil()</script>', occasion_tags:['<b>unsafe</b>']})});
    const script = dom.window.document.querySelector('script').textContent;
    dom.window.eval(script);
    await new Promise(resolve => setTimeout(resolve, 10));
    const box = dom.window.document.querySelector('#daily-scene');
    assert.equal(box.querySelector('img'), null);
    assert.equal(box.querySelector('script'), null);
    assert.match(box.textContent, /<img/);
  } finally { dom.window.close(); }
});

test('telegram start parameter opens gift route', async () => {
  const dom = new JSDOM(readFileSync('app/static/index.html', 'utf8'), {
    url:'https://scena.test/app/', runScripts:'outside-only'
  });
  try {
    const paths = [];
    dom.window.Telegram = {WebApp:{ready(){},expand(){},initData:'',initDataUnsafe:{start_param:'g_testcode'}}};
    dom.window.fetch = async path => {
      paths.push(path);
      return {ok:true,json:async()=>({locked:true,open_at:new Date(Date.now()+3600000).toISOString(),gift:{recipient_name:'A'}})};
    };
    dom.window.eval(readFileSync('app/static/app.js', 'utf8'));
    await new Promise(resolve => setTimeout(resolve, 40));
    assert.ok(paths.includes('/api/gifts/testcode'));
  } finally { dom.window.close(); }
});

test('moment selection is sent intact with the gift', async () => {
  const dom = new JSDOM(readFileSync('app/static/index.html', 'utf8'), {
    url:'https://scena.test/app/#/create', runScripts:'outside-only'
  });
  try {
    let giftBody;
    dom.window.fetch = async (path, options = {}) => {
      let data = {};
      if (path === '/api/cities') data = {cities:[]};
      else if (path.startsWith('/api/users/me')) data = {premium:false};
      else if (path === '/api/scenes/search') data = {scenes:[{id:1,movie_title:'Movie',movie_year:2000,description:'x',occasion_tags:[]}]};
      else if (path === '/api/scenes/1/moments') data = {scene:{movie_title:'Movie',movie_year:2000},moments:[{timecode_label:'0:01:00'},{timecode_label:'1:23:45'}]};
      else if (path === '/api/gifts') { giftBody = JSON.parse(options.body); data = {gift_id:'id',open_url:'https://scena.test/app/#/g/id',qr:'/qr'}; }
      return {ok:true,json:async()=>data};
    };
    dom.window.eval(readFileSync('app/static/app.js','utf8'));
    const wait = () => new Promise(resolve => setTimeout(resolve, 20));
    await wait();
    dom.window.document.querySelector('#btn-pick').click(); await wait();
    dom.window.document.querySelector('.scene-item').click(); await wait();
    const choices = dom.window.document.querySelectorAll('[data-moment]');
    assert.equal(choices.length, 2);
    choices[1].click();
    dom.window.document.querySelector('#btn-create').click(); await wait();
    assert.equal(giftBody.moments['1'], '1:23:45');
  } finally { dom.window.close(); }
});

test('external player links use Telegram openLink, not openTelegramLink', async () => {
  const dom = await screen('/g/open', {'/api/gifts/open': {
    locked:false, gift:{recipient_name:'Аня',view_count:2,theme:'cinema'},
    scenes:[{movie_title:'Фильм',movie_year:2000,timecode_label:'0:01',
      watch_links:[{url:'https://example.com/watch',service:'Player'}]}], replies:[],notes:[]
  }}, 101);
  try {
    const opened = [];
    dom.window.Telegram.WebApp.openLink = url => opened.push(['external', url]);
    dom.window.Telegram.WebApp.openTelegramLink = url => opened.push(['telegram', url]);
    dom.window.document.querySelector('.watch-btn').click();
    assert.deepEqual(opened, [['external','https://example.com/watch']]);
  } finally { dom.window.close(); }
});

test('photo upload failure aborts gift creation', async () => {
  const dom = await screen('/create');
  try {
    const paths = [];
    dom.window.fetch = async (path) => {
      paths.push(path);
      if (path === '/api/scenes/search') return {ok:true,json:async()=>({scenes:[{id:1,movie_title:'Movie',movie_year:2000,description:'x',occasion_tags:[]}]})};
      if (path === '/api/scenes/1/moments') return {ok:true,json:async()=>({scene:{},moments:[]})};
      if (path === '/api/upload') return {ok:false,status:413};
      return {ok:true,json:async()=>({})};
    };
    const wait = () => new Promise(resolve => setTimeout(resolve, 20));
    dom.window.document.querySelector('#btn-pick').click(); await wait();
    dom.window.document.querySelector('.scene-item').click(); await wait();
    Object.defineProperty(dom.window.document.querySelector('#f-photo'), 'files', {value:[new dom.window.File(['x'],'x.jpg')]});
    dom.window.document.querySelector('#btn-create').click(); await wait();
    assert.ok(paths.includes('/api/upload'));
    assert.ok(!paths.includes('/api/gifts'));
    assert.match(dom.window.document.querySelector('#toast').textContent, /Не удалось загрузить фото/);
  } finally { dom.window.close(); }
});
