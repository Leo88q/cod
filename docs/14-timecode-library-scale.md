# 14. Масштабирование библиотеки таймкодов

Документ продолжает [13-timecode-verification.md](13-timecode-verification.md): там описана ручная сверка, здесь — откуда берутся кандидаты, как устроен реестр источников, сколько сцен реально покрыто и как подключить OPUS / OpenSubtitles v2024 на локальной машине.

**Главное правило не изменилось:** импорт любого корпуса создаёт только `status='pending'` кандидаты. `verified=1` появляется исключительно после того, как оператор откроет момент в легальном плеере и нажмёт «✓ Подтвердить» в `#/admin`.

## 1. Что расширено относительно пилота

| Было (пилот) | Стало |
|---|---|
| 20 сцен / 38 SRT | **62 сцены / 105 SRT+CSV** в `full_manifest.json` (по умолчанию) |
| 2 GitHub-репозитория | **4 источника**: 39 861 + 1 300 + 14 SRT и 1 305 CSV |
| 15 IMDb ID | **133 IMDb ID на 140 сцен каталога** (`imdb_map_full.json`), 7 оставлены пустыми |
| только `.srt` | `.srt` + `.csv` из `jblinder/subtext` (startTime/endTime в мс → временный SRT) |
| разовый манифест | `build_srt_index.py` → `srt_index.json` → `build_full_manifest.py` → `full_manifest.json` |
| нет OPUS-адаптера | `import_opus_timecodes.py` для локальных XML-zip OpenSubtitles v2024 |
| нет сводки покрытия | `GET /api/admin/timecode-library` + панель «📚 Библиотека таймкодов» |
| 89 `pending` на 20 сцен | **186 `pending` на 62 сцены** при `--max-per-scene 3` на чистой БД; `--max-per-scene 2` даёт 124 |
| кнопка ⚡ могла поставить `verified=1` без просмотра | ⚡ заблокирована, `bulk-approve` → 403 без `ALLOW_BULK_AUTO_APPROVE=1`, `version_note` обязателен при «✓ Подтвердить» |

## 2. Реестр источников

`data/timecode_library/source_registry.json` + `srt_index.json` (2.6 МБ, только пути, без текстов):

| id | Репозиторий / sha | Формат | Файлов | Лицензия / оговорка |
|---|---|---|---|---|
| `mesu` | `mesutgurlek/Movie-Category-Classification-from-Subtitles` @ `7ebe351e` | SRT | 39 861 | Зеркало OpenSubtitles; текст не сохраняем |
| `imkira` | `imkira3/imkira3Keys` @ `8c1b635c` | SRT | 1 300 | Зеркало OpenSubtitles |
| `fosskers` | `fosskers/subtitles` @ `0511f2fc` | SRT | 14 | В т.ч. `Spirited Away.en.srt` |
| `subtext` | `jblinder/subtext` @ `601f8e44` | CSV | 1 305 | `startTime/endTime/text/title`, конвертируется в SRT |

Опциональные источники в реестре (не интегрированы в песочнице):

- **OPUS / OpenSubtitles v2024** — 7.7M файлов, 40B токенов, 92 языка, `ODC-BY 1.0`. Объектное хранилище `object.pouta.csc.fi` недоступно из песочницы, но доступно с Mac.
- **CineSubBench** — 1 012 фильмов / 8 126 494 timestamped-записей, но лицензия **CC BY-NC-SA 4.0** (только некоммерческое использование), поэтому в продуктовый пайплайн не включён.
- `Helsinki-NLP/OpenSubtitles2024` на HF — только held-out dev/test, не полный корпус; `Arabella25/OpenSubtitles` таймкоды вырезает.

Обновить инвентарь:

```bash
.venv/bin/python scripts/build_srt_index.py
# при лимите GitHub API:
GITHUB_TOKEN=... .venv/bin/python scripts/build_srt_index.py
```

Скрипт читает Git Trees API, не скачивает сами субтитры и не коммитит тексты.

## 3. IMDb-справочник на весь каталог

`data/timecode_library/imdb_map_full.json`:

- ключ `«название|год»` — так различаются, например, «Начало» 1970 (`tt0066124`) и «Начало» 2010 (`tt1375666`);
- **133 из 140** сцен получили `imdb_id`; источники: `API-Movies/videocdn` (RU-названия), `m-bain/CondensedMovies`, ручные оверрайды;
- 7 ID не выдумывали и оставили `null`: «Бумер 2», «Груз 200», «Стиляги», «Духless», «О чём говорят мужчины 2», «Десять причин моей ненависти», «После дождя». Их оператор может заполнить при ручной сверке;
- `tmdb_id` зарезервирован, но пока `null`;
- в `scenes` добавлены колонки `imdb_id`/`tmdb_id` с миграцией в `app/db.py`; `apply_imdb_map()` понимает оба формата ключей.

## 4. Полный манифест и защита от ложных совпадений

`scripts/build_full_manifest.py` сопоставляет English title из справочника с нормализованными именами файлов в `srt_index.json`, выбирает до 2 файлов из разных источников и добавляет наши ключевые слова/описания.

`data/timecode_library/full_manifest.json`:

```json
{
  "catalog_scenes": 140,
  "matched_scenes": 62,
  "unmatched_scenes": 78,
  "excluded_false_matches": 6,
  "ambiguous_scenes": 5,
  "subtitle_files": 105,
  "sources_used": ["fosskers", "imkira", "mesu", "subtext"],
  "pilot_curated_keywords": 20
}
```

- **20 сцен** получили кураторские keyword-группы из пилота; остальные — английские слова из названия + общий эмоциональный словарь. Это кандидаты, а не «истина».
- `ambiguous: true` + `match_note` ставятся, если имя файла без года, совпадение только по префиксу/без артикля или название легко перепутать. Метка попадает в `moment_label`, поэтому видна в очереди админки.
- **6 совпадений исключены сознательно**: «Остров» 2006 (в корпусе другой «The Island»), «Дюна» 2021 (SRT от версии 1984), «Человек-паук: Через вселенные» (префикс указывает на другой фильм) и все три части «Властелина колец» (найден только общий `The Lord of the Rings.srt` без части/года).
- 78 сцен без SRT — в основном советское/российское кино и фильмы 2019–2023, которых нет в старых GitHub-зеркалах. Их закрывает OPUS или ручной поиск.

Пересборка:

```bash
.venv/bin/python scripts/build_full_manifest.py
```

## 5. Импорт: полный, пилот, dry-run

```bash
# по умолчанию: full_manifest.json + imdb_map_full.json, максимум 3 кандидата на сцену
.venv/bin/python scripts/import_timecode_library.py

# только кураторский пилот
.venv/bin/python scripts/import_timecode_library.py --pilot

# посмотреть кандидатов без записи в БД
.venv/bin/python scripts/import_timecode_library.py --dry-run --max-per-scene 2

# все кандидаты без ограничения, перекачать кэш
.venv/bin/python scripts/import_timecode_library.py --max-per-scene 0 --refresh
```

Импортёр скачивает SRT/CSV в gitignored `data/timecode_library/cache/`, конвертирует CSV в SRT, зовёт `fetch_timecodes.py` и пишет только `start/end + sha1 + source + source_license + confidence/consensus + moment_label`. Текст реплик не сохраняется. Уникальный индекс `idx_tc_candidates_dedup` защищает от дублей при повторном запуске.

### GitHub 403 / rate limit

Анонимный `api.github.com` даёт всего **60 запросов в час**, а полный манифест — 105 файлов. Поэтому загрузчик теперь работает в три шага:

1. `raw.githubusercontent.com` — основной канал для публичных blob, **не тратит REST-квоту**;
2. `api.github.com` с `GITHUB_TOKEN`/`GH_TOKEN` — 5000 запросов/час;
3. `api.github.com` без токена — fallback для сетей, где raw закрыт.

Если на Mac всё равно появилось `HTTP Error 403: rate limit exceeded`:

```bash
# вариант 1: GitHub CLI уже залогинен
export GITHUB_TOKEN=$(gh auth token)
.venv/bin/python scripts/import_timecode_library.py

# вариант 2: ввести токен вручную, без вывода на экран
read -rs GITHUB_TOKEN
export GITHUB_TOKEN
.venv/bin/python scripts/import_timecode_library.py
```

Достаточно fine-grained token без дополнительных прав: он только поднимает лимит на чтение публичных репозиториев. Не присылай токен в чат и не коммить его; `.env` и кэш уже в `.gitignore`. Уже скачанные файлы берутся из кэша, поэтому повторный запуск не делает 105 сетевых запросов.

## 6. OPUS / OpenSubtitles v2024 на Mac

Песочница не имеет доступа к `object.pouta.csc.fi`, поэтому корпус скачивается на локальной машине:

```bash
mkdir -p ~/Desktop/opus-xml
curl -L -o ~/Desktop/opus-xml/en.zip \
  https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2024/xml/en.zip
curl -L -o ~/Desktop/opus-xml/ru.zip \
  https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2024/xml/ru.zip
```

Затем:

```bash
cd /Users/zlata/Desktop/timecod
.venv/bin/python scripts/import_opus_timecodes.py \
  --opus-dir ~/Desktop/opus-xml --dry-run --limit-files 20
.venv/bin/python scripts/import_opus_timecodes.py \
  --opus-dir ~/Desktop/opus-xml --max-per-scene 3
```

Адаптер:

- читает XML-архивы потоково (`zipfile` + `ElementTree.iterparse`);
- понимает `<s time_start=... time_end=...>` и OPUS-разметку `<time value="HH:MM:SS,mmm"/>` + `<w>`;
- выводит IMDb ID из пути `en/<id>/<id>.xml`, сопоставляет с `imdb_map_full.json`;
- считает консенсус между `en.zip` и `ru.zip` (±10 сек);
- пишет `source='opus://<zip>/<xml>'`, `source_license` с `ODC-BY 1.0`, `status='pending'`;
- **не** запускает авто-одобрение и не сохраняет текст.

Для больших языковых zip имеет смысл сначала `--limit-files 20`, затем снять ограничение. Скрипт не требует `GITHUB_TOKEN` и работает офлайн после скачивания архивов.

## 7. Сводка покрытия

```bash
curl -s -H 'X-Admin-Token: local-admin' \
  http://localhost:8000/api/admin/timecode-library
```

Возвращает: счётчики сцен/IMDb/реальных таймкодов/`verified`, кандидатов по статусам, топ источников в БД, реестр источников, статистику `full_manifest.json` / `imdb_map_full.json`, размеры файлов библиотеки. Панель «📚 Библиотека таймкодов» в `#/admin` показывает те же цифры.

## 8. Что осталось за рамками

- Реальных `verified=1` таймкодов пока **0**: это и есть работа оператора, ради которой всё строилось.
- 78 сцен без SRT в GitHub-зеркалах; для части из них есть официальные бесплатные полные версии и ручное создание `pending`-кандидата через `POST /api/admin/timecodes/manual` (см. [docs/15-moderation-links.md](docs/15-moderation-links.md)). 7 сцен без IMDb ID; `tmdb_id` не заполнен.
- Прямые `?t=` deep links теперь генерируются для VK Видео / Rutube: 10 кураторских Rutube-версий Мосфильм и вставленный `watch_url` на этих площадках. Остальные карточки получают поиск VK/Rutube + локальную команду. См. [docs/15-moderation-links.md](docs/15-moderation-links.md).
- Разные издания фильма дают разный таймкод — поэтому `version_note` обязателен при подтверждении (и на бэкенде, и в UI), а `ambiguous` кандидаты нужно проверять особенно внимательно. Авто-одобрение консенсуса отключено по умолчанию: `bulk-approve` → 403 без `ALLOW_BULK_AUTO_APPROVE=1`, кнопка ⚡ заблокирована.
- Перед коммерческим запуском нужна юридическая проверка: `ODC-BY` у OPUS не отменяет прав правообладателей на тексты субтитров; CineSubBench — NC-лицензия.

## 9. Проверки

- `109 passed` pytest, `13 passed` Node/jsdom.
- `bash scripts/check.sh` с живым сервером: 5/5 шагов, `SMOKE OK`, `ВСЁ ЗЕЛЁНО ✅`.
- `git diff --exit-code db/seed_catalog.sql` — чисто.
- `full_manifest.json` / `imdb_map_full.json` / OPUS-адаптер / admin coverage endpoint / raw→API fallback загрузчика покрыты отдельными тестами.
- Полный импорт на чистой БД: **186 `pending` кандидатов** на 62 сцены, 133 IMDb ID, `verified=0` (с `--max-per-scene 2` — 124).
