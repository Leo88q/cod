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
    '/api/admin/timecodes?status=pending': {candidates:[]},
'/api/admin/moderation': {stats:{total:146,pending:44,needs_curator:102,verified:0,with_real_timecode:44,with_open_url:10,with_local_command:44,by_category:{love_confession:17,female_friendship:8}},
      categories:[{id:'love_confession',label:'Признание в любви'},{id:'female_friendship',label:'Женская дружба / сестринство'},{id:'support_hope',label:'Поддержка'}],
      items:[{scene_id:1,movie_title:'Ирония судьбы, или С лёгким паром!',movie_year:1975,imdb_id:'tt0073179',status:'pending',status_label:'к проверке',description:'Новогодняя Москва',why_fits:'',
        category:'love_confession',category_label:'Признание в любви',fragment_type:'monologue',fragment_type_label:'Одна длинная реплика / монолог',secondary_intents:[],curated_anchor:'Робкое объяснение в чувствах',intent_confidence:0.95,intent_source:'manual',
        candidate_id:7,candidate_status:'pending',candidate_source:'https://example.com/srt',intent_score:0.62,proposal_kind:'semantic',proposal_note:'Окно отобрано под категорию',reason:'Признание в любви: 3 смысловых совпадения, монолог, 32 сек',
        proposed_label:'0:32:56',proposed_seconds:1976,proposed_end_label:'0:33:21',proposed_end_seconds:2001,moment_label:'Женя у двери',
        open_url:'https://www.youtube.com/watch?v=y5CQr_D-NZg&t=1976s',open_provider:'youtube_mosfilm',open_kind:'youtube',open_note:'Официальная версия',
        source_url:'https://www.youtube.com/watch?v=y5CQr_D-NZg&t=1976s',local_command:'mpv --start=0:32:56 "/path/to/film.mkv"',search_url:'https://www.kinopoisk.ru/index.php?kp_query=x',youtube_search_url:'https://www.youtube.com/results?search_query=x',imdb_url:'https://www.imdb.com/title/tt0073179/',scene_version_note:''}]},
    '/api/admin/timecode-library': {catalog:{total:146,with_imdb:133,with_timecode:0,verified:0},
      candidates:{by_status:{pending:0},scenes_with_candidates:0,total:0},
      moderation:{official_videos:12,scenes_with_official_deep:10,requires_version_check:true,verified_by_human:false,sources:['mosfilm_youtube','mosfilm_cinema'],
        targets:[{scene_id:1,key:'Ирония судьбы, или С лёгким паром!|1975',movie_title:'Ирония судьбы, или С лёгким паром!',movie_year:1975,imdb_id:'tt0073179',url:'https://www.youtube.com/watch?v=y5CQr_D-NZg&t=0s',provider:'youtube_mosfilm',has_deep_link:true,version_hint:'Реставрация Мосфильм',evidence_url:'https://www.youtube.com/watch?v=sDdrTIp8mxs',pending_candidates:0}]},
      sources:{registry:[{id:'mesu',count:39861,license:'OpenSubtitles mirror'}],optional:[],top_in_db:[]},
      manifests:{pilot_scenes:20,full:{matched_scenes:62,catalog_scenes:140,subtitle_files:105},imdb_map:{with_imdb:133},unmatched_count:78,excluded_count:6,ambiguous_count:5},
      files:[],note:'Ручная сверка обязательна.'}
  });
  try {
    dom.window.document.querySelector('#adm-load').click();
    await new Promise(resolve => setTimeout(resolve, 40));
    assert.ok(dom.window.document.querySelector('.notification-retry'));
    assert.match(dom.window.document.querySelector('#adm-body').textContent, /notification_retry/);
    assert.ok(dom.window.document.querySelector('#tc-bulk').disabled);
    assert.match(dom.window.document.querySelector('#adm-body').textContent, /Авто-одобрение отключено/);
    assert.match(dom.window.document.querySelector('#adm-body').textContent, /Библиотека таймкодов/);
    assert.match(dom.window.document.querySelector('#adm-body').textContent, /полный манифест: 62 из 140/);
    assert.match(dom.window.document.querySelector('#adm-body').textContent, /Легальные бесплатные версии: 10 сцен/);
    assert.match(dom.window.document.querySelector('#adm-body').textContent, /Легальные полные версии для ручного поиска момента/);
    assert.ok(dom.window.document.querySelector('.md-target'));
    assert.match(dom.window.document.querySelector('#adm-body').textContent, /Модерация таймкодов — 146 сцен/);
    assert.ok(dom.window.document.querySelector('.mod-item'));
    assert.match(dom.window.document.querySelector('.mod-item').dataset.open, /t=1976s/);
    assert.match(dom.window.document.querySelector('#adm-body').textContent, /Признание в любви/);
    assert.match(dom.window.document.querySelector('#adm-body').textContent, /к проверке/);
    assert.match(dom.window.document.querySelector('#adm-body').textContent, /сцен с доказательным фрагментом/);
    assert.ok(dom.window.document.querySelector('.mod-category'));
    assert.match(dom.window.document.querySelector('.mod-category').textContent, /Признание в любви/);
    assert.ok(dom.window.document.querySelector('.mod-anchor'));
  } finally { dom.window.close(); }
});

test('admin timecode queue supports manual verification fields', async () => {
  const dom = await screen('/admin', {
    '/api/admin/overview': {users:1,scenes:1,gifts:0,shops:0,cards:0,revenue_rub:0},
    '/api/admin/reports': {reports:[]},
    '/api/admin/metrics': {worker:{status:'ok',age_seconds:0},pending_notifications:0,failed_notifications:0},
    '/api/admin/notifications/failed': {notifications:[]},
    '/api/admin/audit': {entries:[]},
    '/api/admin/analytics': {funnel:{}},
    '/api/admin/scenes?status=draft': {scenes:[]},
    '/api/admin/scenes?status=approved&verified=0': {scenes:[]},
    '/api/admin/timecodes?status=pending': {candidates:[{
      id:7, movie_title:'Форрест Гамп', movie_year:1994, imdb_id:'tt0109830',
      timecode_label:'0:50:19', timecode_end_label:'0:50:56', service:'kinopoisk',
      description:'Форрест бежит через страну', why_fits:'для поддержки',
      source:'https://github.com/o/r/blob/sha/Forrest.srt',
      source_license:'OpenSubtitles community subtitles, GitHub mirror',
      line_index:512, confidence:1.25, consensus:2, auto:1,
      moment_label:'момент, где Форрест бежит', watch_links:[],
      moderation_links:[
        {kind:'official_deep',priority:1,label:'▶ youtube_mosfilm · 0:50:19',url:'https://www.youtube.com/watch?v=y5CQr_D-NZg&t=3019s',note:'Сверь издание'},
        {kind:'local',priority:3,label:'⏱ Открыть локальный файл на моменте',commands:[{app:'mpv',command:'mpv --start=0:50:19 "/path/to/film.mkv"'}]}
      ]
    }]},
'/api/admin/moderation': {stats:{total:146,pending:44,needs_curator:102,verified:0,with_real_timecode:44,with_open_url:10,with_local_command:44,by_category:{love_confession:17,female_friendship:8}},
      categories:[{id:'love_confession',label:'Признание в любви'},{id:'female_friendship',label:'Женская дружба / сестринство'},{id:'support_hope',label:'Поддержка'}],
      items:[{scene_id:1,movie_title:'Ирония судьбы, или С лёгким паром!',movie_year:1975,imdb_id:'tt0073179',status:'pending',status_label:'к проверке',description:'Новогодняя Москва',why_fits:'',
        category:'love_confession',category_label:'Признание в любви',fragment_type:'monologue',fragment_type_label:'Одна длинная реплика / монолог',secondary_intents:[],curated_anchor:'Робкое объяснение в чувствах',intent_confidence:0.95,intent_source:'manual',
        candidate_id:7,candidate_status:'pending',candidate_source:'https://example.com/srt',intent_score:0.62,proposal_kind:'semantic',proposal_note:'Окно отобрано под категорию',reason:'Признание в любви: 3 смысловых совпадения, монолог, 32 сек',
        proposed_label:'0:32:56',proposed_seconds:1976,proposed_end_label:'0:33:21',proposed_end_seconds:2001,moment_label:'Женя у двери',
        open_url:'https://www.youtube.com/watch?v=y5CQr_D-NZg&t=1976s',open_provider:'youtube_mosfilm',open_kind:'youtube',open_note:'Официальная версия',
        source_url:'https://www.youtube.com/watch?v=y5CQr_D-NZg&t=1976s',local_command:'mpv --start=0:32:56 "/path/to/film.mkv"',search_url:'https://www.kinopoisk.ru/index.php?kp_query=x',youtube_search_url:'https://www.youtube.com/results?search_query=x',imdb_url:'https://www.imdb.com/title/tt0073179/',scene_version_note:''}]},
    '/api/admin/timecode-library': {catalog:{total:146,with_imdb:133,with_timecode:0,verified:0},
      candidates:{by_status:{pending:1},scenes_with_candidates:1,total:1},
      moderation:{official_videos:12,scenes_with_official_deep:10,requires_version_check:true,verified_by_human:false,sources:['mosfilm_youtube','mosfilm_cinema'],
        targets:[{scene_id:1,key:'Ирония судьбы, или С лёгким паром!|1975',movie_title:'Ирония судьбы, или С лёгким паром!',movie_year:1975,imdb_id:'tt0073179',url:'https://www.youtube.com/watch?v=y5CQr_D-NZg&t=0s',provider:'youtube_mosfilm',has_deep_link:true,version_hint:'Реставрация Мосфильм',evidence_url:'https://www.youtube.com/watch?v=sDdrTIp8mxs',pending_candidates:0}]},
      sources:{registry:[{id:'mesu',count:39861,license:'OpenSubtitles mirror'}],optional:[],top_in_db:[]},
      manifests:{pilot_scenes:20,full:{matched_scenes:62,catalog_scenes:140,subtitle_files:105},imdb_map:{with_imdb:133},unmatched_count:78,excluded_count:6,ambiguous_count:5},
      files:[],note:'Ручная сверка обязательна.'}
  });
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
    assert.equal(doc.querySelector('.tc-service').value, 'kinopoisk');
    assert.equal(doc.querySelector('.tc-copy').dataset.tc, '0:50:19–0:50:56');
    assert.match(doc.querySelector('#adm-body').textContent, /Наш момент: Форрест бежит через страну/);
    doc.querySelector('.tc-open').click();
    assert.ok(opened && opened.includes('kinopoisk'));
    assert.ok(doc.querySelector('.md-link'), 'должна быть легальная deep link на момент');
    assert.match(doc.querySelector('.md-link').dataset.url, /t=3019s/);
    assert.ok(doc.querySelector('.md-copy'), 'должна быть команда для локального файла');
    assert.match(doc.querySelector('#adm-body').textContent, /Официальная бесплатная версия найдена/);
    doc.querySelector('.md-start').value = '0:32:56';
    doc.querySelector('.md-end').value = '0:33:21';
    doc.querySelector('.md-version').value = 'Мосфильм, реставрация';
    doc.querySelector('.md-label').value = 'Женя у двери';
    doc.querySelector('.md-add').click();
    await new Promise(resolve => setTimeout(resolve, 60));
    assert.ok(manualPosted, 'ручной момент должен уйти на /timecodes/manual');
    assert.equal(manualPosted.movie_title, 'Ирония судьбы, или С лёгким паром!');
    assert.equal(manualPosted.timecode_label, '0:32:56');
    assert.equal(manualPosted.timecode_end_label, '0:33:21');
    assert.equal(manualPosted.version_note, 'Мосфильм, реставрация');
    // adm-load перезагрузил разметку после успешного добавления
    doc.querySelector('.tc-start').value = '0:50:20';
    doc.querySelector('.tc-approve').click();
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(posted, null, 'без версии издания запрос не должен уходить');
    doc.querySelector('.tc-version').value = 'театральная, Kinopoisk 2024';
    doc.querySelector('.tc-approve').click();
    await new Promise(resolve => setTimeout(resolve, 40));
    assert.ok(posted, 'Approve должен отправить POST');
    assert.equal(posted.body.action, 'approve');
    assert.equal(posted.body.timecode_label, '0:50:20');
    assert.equal(posted.body.timecode_end_label, '0:50:56');
    assert.equal(posted.body.service, 'kinopoisk');
    assert.equal(posted.body.version_note, 'театральная, Kinopoisk 2024');
    assert.equal(posted.body.moment_label, 'момент, где Форрест бежит');
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
