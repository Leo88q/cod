const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { loveTableMock } = require('./fixtures/loveConfessionBatch.cjs');
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

function adminMocks(extra = {}) {
  return {
    '/api/admin/overview': {users:1,scenes:1,gifts:0,shops:1,cards:0,revenue_rub:0},
    '/api/admin/reports': {reports:[]},
    '/api/admin/metrics': {worker:{status:'degraded',age_seconds:10},pending_notifications:1,failed_notifications:1},
    '/api/admin/notifications/failed': {notifications:[{id:2,kind:'reply',attempts:8}]},
    '/api/admin/audit': {entries:[{created_at:'2026-11-04',action:'notification_retry',actor:'admin-token',resource:'2'}]},
    '/api/admin/analytics': {funnel:{}},
    '/api/admin/scenes?status=draft': {scenes:[]},
    '/api/admin/scenes?status=approved&verified=0': {scenes:[]},
    '/api/admin/timecodes?status=pending': {candidates:[]},
    '/api/admin/timecode-library': {
      catalog:{total:146,with_imdb:133,with_timecode:0,verified:0},
      candidates:{by_status:{pending:0},scenes_with_candidates:0,total:0},
      moderation:{official_videos:10,scenes_with_official_deep:10,requires_version_check:true,verified_by_human:false,
        sources:['rutube_russia1'],targets:[{scene_id:1,key:'Ирония судьбы, или С лёгким паром!|1975',
          movie_title:'Ирония судьбы, или С лёгким паром!',movie_year:1975,imdb_id:'tt0073179',
          url:'https://rutube.ru/video/2d0899d6388926b7caa66a09e9073572/?t=0',provider:'rutube_russia1',
          has_deep_link:true,version_hint:'Реставрация',evidence_url:'https://rutube.ru/video/2d0899d6388926b7caa66a09e9073572/',
          pending_candidates:0}]},
      sources:{registry:[{id:'mesu',count:39861,license:'OpenSubtitles mirror'}],optional:[],top_in_db:[]},
      manifests:{pilot_scenes:20,full:{matched_scenes:62,catalog_scenes:140,subtitle_files:105},
        imdb_map:{with_imdb:133},unmatched_count:78,excluded_count:6,ambiguous_count:5},
      files:[],note:'Ручная сверка обязательна.'},
    '/api/admin/moderation/love-confession/table': loveTableMock(),
    ...extra,
  };
}

test('admin renders one compact love table with direct links and editable timecodes', async () => {
  const dom = await screen('/admin', adminMocks());
  try {
    const doc = dom.window.document;
    doc.querySelector('#adm-load').click();
    await new Promise(resolve => setTimeout(resolve, 40));
    const text = doc.querySelector('#adm-body').textContent;
    const table = doc.querySelector('#moderation-board');
    const tableText = table.textContent;
    assert.ok(doc.querySelector('.notification-retry'));
    assert.match(text, /notification_retry/);
    assert.ok(doc.querySelector('#tc-bulk').disabled);
    assert.match(text, /Авто-одобрение отключено/);
    assert.match(text, /Библиотека таймкодов/);
    assert.match(text, /полный манифест: 62 из 140/);
    assert.match(text, /Легальные бесплатные версии: 10 сцен/);
    assert.ok(doc.querySelector('.md-target'));
    const details = doc.querySelector('#adm-body details');
    assert.ok(details, 'служебные панели спрятаны в details');
    assert.equal(details.hasAttribute('open'), false);

    assert.match(tableText, /Признание в любви и чувства/);
    assert.match(tableText, /2 фильмов · 1 прямых ссылок VK\/Rutube · 1 полей под ссылку/);
    assert.match(tableText, /version_note обязателен/);
    assert.match(tableText, /Ссылка на фильм/);
    assert.match(tableText, /Версия издания/);
    assert.equal(doc.querySelectorAll('.love-row').length, 2);
    assert.equal(doc.querySelectorAll('.love-start').length, 2);
    assert.equal(doc.querySelector('.love-row .love-start').value, '0:43:04');
    assert.equal(doc.querySelector('.love-row .love-end').value, '0:43:58');
    assert.equal(doc.querySelectorAll('.love-url')[0].value, 'https://rutube.ru/video/abc/');
    assert.equal(doc.querySelectorAll('.love-url')[1].value, '');
    assert.ok(doc.querySelector('.love-check'));
    assert.ok(doc.querySelector('.love-save'));
    assert.ok(doc.querySelector('.love-confirm'));
    assert.ok(doc.querySelector('.love-reject'));
    assert.match(tableText, /к проверке/);
    // Никаких поисковых URL, «Мосфильм»-префиксов и старых карточек в основной таблице.
    assert.doesNotMatch(tableText, /search|Мосфильм|vk_search|ЧТО СМОТРЕТЬ|ВНЕШНЯЯ ПОДСКАЗКА/);
    assert.equal(table.querySelector('.mod-item'), null);
    assert.equal(table.querySelector('.mod-time-badge'), null);
    assert.equal(doc.querySelector('#mod-filter'), null);
    assert.equal(doc.querySelector('.cur-start'), null);
    assert.doesNotMatch(tableText, /найди момент сам|Таймкоды всех 50 отрывков/);
  } finally { dom.window.close(); }
});

test('admin timecode queue supports manual verification fields', async () => {
  const dom = await screen('/admin', adminMocks({
    '/api/admin/timecodes?status=pending': {candidates:[{
      id:7, movie_title:'Форрест Гамп', movie_year:1994, imdb_id:'tt0109830',
      timecode_label:'0:50:19', timecode_end_label:'0:50:56', service:'vkvideo',
      description:'Форрест бежит через страну', why_fits:'для поддержки',
      source:'https://github.com/o/r/blob/sha/Forrest.srt',
      source_license:'OpenSubtitles community subtitles, GitHub mirror',
      line_index:512, confidence:1.25, consensus:2, auto:1,
      moment_label:'момент, где Форрест бежит', watch_links:[],
      moderation_links:[
        {kind:'official_deep',priority:2,label:'▶ Rutube · 0:50:19',url:'https://rutube.ru/video/abc/?t=3019',note:'Сверь издание'},
        {kind:'search',priority:1,label:'🔍 VK Видео · Форрест Гамп',url:'https://vkvideo.ru/search?query=x',note:'Поиск'},
        {kind:'local',priority:3,label:'⏱ Открыть локальный файл на ориентире',commands:[{app:'mpv',command:'mpv --start=0:50:19 "/path/to/film.mkv"'}]}
      ]
    }]}
  }));
  try {
    dom.window.localStorage.setItem('scena_admin_token', 'test-admin');
    let opened = null, posted = null, manualPosted = null;
    dom.window.open = (url) => { opened = url; };
    const originalFetch = dom.window.fetch;
    dom.window.fetch = async (path, opts) => {
      if (opts && opts.method === 'POST' && String(path).includes('/review')) {
        posted = { path, body: JSON.parse(opts.body) };
        return { ok: true, json: async () => ({ok:true}) };
      }
      if (opts && opts.method === 'POST' && String(path).includes('/timecodes/manual')) {
        manualPosted = JSON.parse(opts.body);
        return { ok: true, json: async () => ({ok:true,id:999}) };
      }
      return originalFetch(path, opts);
    };
    dom.window.document.querySelector('#adm-load').click();
    await new Promise(resolve => setTimeout(resolve, 40));
    const doc = dom.window.document;
    assert.equal(doc.querySelector('.tc-start').value, '0:50:19');
    assert.equal(doc.querySelector('.tc-end').value, '0:50:56');
    assert.equal(doc.querySelector('.tc-label').value, 'момент, где Форрест бежит');
    assert.equal(doc.querySelector('.tc-service').value, 'vkvideo');
    assert.equal(doc.querySelector('.tc-copy').dataset.tc, '0:50:19–0:50:56');
    assert.match(doc.querySelector('#adm-body').textContent, /Наш момент: Форрест бежит через страну/);
    doc.querySelector('.tc-open').click();
    assert.ok(opened && opened.includes('vkvideo.ru'));
    assert.ok(doc.querySelector('.md-link'), 'должна быть легальная deep link на момент');
    assert.match(doc.querySelector('.md-link').dataset.url, /t=3019/);
    assert.ok(doc.querySelector('.md-copy'), 'должна быть команда для локального файла');
    assert.match(doc.querySelector('#adm-body').textContent, /Официальная бесплатная версия найдена/);
    doc.querySelector('.md-start').value = '0:32:56';
    doc.querySelector('.md-end').value = '0:33:21';
    doc.querySelector('.md-version').value = 'реставрация';
    doc.querySelector('.md-label').value = 'Женя у двери';
    doc.querySelector('.md-add').click();
    await new Promise(resolve => setTimeout(resolve, 60));
    assert.ok(manualPosted, 'ручной момент должен уйти на /timecodes/manual');
    assert.equal(manualPosted.movie_title, 'Ирония судьбы, или С лёгким паром!');
    assert.equal(manualPosted.timecode_label, '0:32:56');
    assert.equal(manualPosted.timecode_end_label, '0:33:21');
    assert.equal(manualPosted.version_note, 'реставрация');
    // adm-load перезагрузил разметку после успешного добавления
    doc.querySelector('.tc-start').value = '0:50:20';
    doc.querySelector('.tc-approve').click();
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(posted, null, 'без версии издания запрос не должен уходить');
    doc.querySelector('.tc-version').value = 'театральная, VK Видео 2024';
    doc.querySelector('.tc-approve').click();
    await new Promise(resolve => setTimeout(resolve, 40));
    assert.ok(posted, 'Approve должен отправить POST');
    assert.equal(posted.body.action, 'approve');
    assert.equal(posted.body.timecode_label, '0:50:20');
    assert.equal(posted.body.timecode_end_label, '0:50:56');
    assert.equal(posted.body.service, 'vkvideo');
    assert.equal(posted.body.version_note, 'театральная, VK Видео 2024');
    assert.equal(posted.body.moment_label, 'момент, где Форрест бежит');
  } finally { dom.window.close(); }
});

test('admin love table saves rows without verifying and confirms with version_note', async () => {
  const posts = [];
  const dom = await screen('/admin', adminMocks());
  try {
    const doc = dom.window.document;
    doc.querySelector('#adm-load').click();
    await new Promise(resolve => setTimeout(resolve, 40));
    let opened = null;
    dom.window.open = (url) => { opened = url; };
    const originalFetch = dom.window.fetch;
    dom.window.fetch = async (path, opts) => {
      if (opts && opts.method === 'POST') {
        posts.push({ path: String(path), body: JSON.parse(opts.body) });
        return { ok: true, json: async () => ({ok:true}) };
      }
      return originalFetch(path, opts);
    };
    const tick = () => new Promise(resolve => setTimeout(resolve, 40));
    let row = doc.querySelectorAll('.love-row')[0];
    row.querySelector('.love-start').value = '0:43:10';
    row.querySelector('.love-check').click();
    assert.ok(opened && opened.includes('t=2590'), 'прямая ссылка открывается с таймкодом');

    row.querySelector('.love-save').click();
    await tick();
    const saved = posts.find(p => p.path.includes('/edit'));
    assert.ok(saved, 'сохранение строки уходит на /edit');
    assert.equal(saved.path, '/api/admin/moderation/love-confession/501/edit');
    assert.equal(saved.body.candidate_id, 501);
    assert.equal(saved.body.timecode_label, '0:43:10');
    assert.equal(saved.body.film_url, 'https://rutube.ru/video/abc/');
    assert.equal(saved.body.verified, undefined, 'сохранение не ставит verified');

    row = doc.querySelectorAll('.love-row')[0];
    row.querySelector('.love-confirm').click();
    await tick();
    assert.equal(posts.some(p => p.path.includes('/confirm')), false,
      'без version_note подтверждение не уходит');

    row = doc.querySelectorAll('.love-row')[0];
    row.querySelector('.love-version').value = 'Rutube, реставрация';
    row.querySelector('.love-confirm').click();
    await tick();
    const confirmed = posts.find(p => p.path.includes('/confirm'));
    assert.ok(confirmed, 'подтверждение уходит на /confirm');
    assert.equal(confirmed.body.action, 'confirm');
    assert.equal(confirmed.body.version_note, 'Rutube, реставрация');
    assert.match(confirmed.body.watch_url, /rutube\.ru\/video\/abc\/\?t=2584/);

    row = doc.querySelectorAll('.love-row')[0];
    row.querySelector('.love-reject').click();
    await tick();
    const rejected = posts.filter(p => p.path.includes('/confirm')).pop();
    assert.equal(rejected.body.action, 'reject');
    assert.equal(rejected.body.version_note, undefined);
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
