# 15. Открываем фильм для модерации: VK Видео + Rutube

> **Актуальный основной экран** — компактная таблица на 50 строк «признание в любви и чувства»
> ([18-curator-workflow.md](18-curator-workflow.md), [20-love-confession-batch.md](20-love-confession-batch.md)):
> там только точные прямые ссылки VK/Rutube, а для фильмов без точного ID — пустое редактируемое поле.
> Поисковые URL и «Мосфильм»-префиксы в таблицу не попадают. Ниже описана **служебная очередь
> всех 146 сцен**, которая в админке свёрнута в закрытый `<details>`.

## Решение куратора

Площадки переориентированы: **VK Видео — основной источник, Rutube — второй**.
Из карточек модерации и из пользовательских подсказок «где смотреть» удалены
YouTube, Vimeo, Кинопоиск, ivi, Okko, IMDb/TMDB и отдельные страницы студий.
LordFilm / «зеркала» / неофициальные плееры по-прежнему не добавляем; обход
оплаты и геоблокировок не используется.

Официальные площадки «Мосфильма»: [группа VK](https://vk.com/mosfilm_official),
[VK Видео](https://vkvideo.ru/@mosfilm_official), [Rutube-канал](https://rutube.ru/u/mosfilmofficial/).
Сама студия также описывает VK Видео и Rutube как свои онлайн-ресурсы в
[новости об аудитории](https://www.mosfilm.ru/about/news/onlayn-resursy-mosfilma-rost-auditorii-v-rossii-i-za-rubezhom/).

## Алгоритм

1. **Целевая секунда известна.** `timecode_seconds` у доказательного кандидата или
   уникальный `starting_seconds` у ориентира. Конец отрезка показываем рядом,
   но ссылка ведёт на начало.
2. **VK Видео — первая ссылка у всех 146 сцен.** Если в реестре есть
   `vk_video` ID, строим `https://vkvideo.ru/video-<owner>_<id>?t=<секунды>s`.
   Иначе даём поиск `https://vkvideo.ru/search?query=Мосфильм+<название>+<год>+полный+фильм`
   и подсказываем открыть официальный канал. Формат `?t=...` у VK-страниц
   подтверждён разбором плеера в
   [feedsweep #1668](https://github.com/macieklamberski/feedsweep/pull/1668);
   для поисковой ссылки автоперемотка не гарантируется.
3. **Rutube — вторая ссылка.** Для 14 кураторских полных версий (10 «Мосфильм»
   + 4 «Россия-1») строим `https://rutube.ru/video/<uuid>/?t=<секунды>`; параметр `t` описан в
   [документации Rutube Player API](https://github.com/evikza/Rutube-Player-JS-API-Doc)
   и [плагине Bg RuTube Embed](https://wordpress.com/plugins/bg-rutube-embed).
   Остальным сценам даём поиск `https://rutube.ru/search/?query=...` по
   официальному каналу.
4. **`watch_url` оператора** нормализуется только если уже ведёт на VK/Rutube:
   старый `t` заменяется на `?t=<секунды>s` для VK и `?t=<секунды>` для Rutube.
   Ссылки на другие хосты больше не выдаются.
5. **Локальная легальная копия** остаётся как независимый способ открытьориентир:
   `mpv --start=...`, VLC, IINA. Это не сторонний источник, а свой файл оператора.
6. **Ранжирование:** VK (0/1) → Rutube (2/3) → `watch_url` VK/Rutube (4) →
   локальная команда (5). Других `kind` и доменов в `moderation_links[]` нет.
7. **Ручное подтверждение не изменилось.** `verified=1` ставится только кнопкой
   «✓ Подтвердить», `version_note` обязателен: реставрация 4K, ТВ-монтаж,
   серия и цветная/чб версия могут сдвинуть минуты. Orientир и deep link
   проверенным таймкодом не считаются.

## Что в данных

`data/timecode_library/official_free_videos.json`:

- `sources`: `vk_mosfilm`, `rutube_mosfilm`, `rutube_russia1`;
- `provider_templates`: `vk_video`, `rutube`;
- 14 best-effort Rutube `video_id`: 10 «Мосфильм» («Ирония судьбы»,
  «Иван Васильевич», «Москва слезам не верит», «Служебный роман»,
  «Операция «Ы»», «Любовь и голуби», «Кин-дза-дза!», «Девчата»,
  «Белое солнце пустыни», «Война и мир») + 4 «Россия-1» («Афоня»,
  «Кавказская пленница», «Курьер», «Чародеи»);
- из этих 14 точных ID 9 попадают в блок «признание в любви и чувства»;
  остальные 41 строка блока оставляют поле ссылки пустым,
- `requires_version_check: true`, `verified_by_human: false`.

ID взяты из поисковой выдачи официального канала, но человеком ещё не сверены.
Куратор должен проверить канал, что это полный фильм, а не трейлер/фрагмент,
совпадает ли серия/часть, и подходит ли издание под таймкод.

## UI и API

- `GET /api/admin/moderation`: у всех 146 карточек есть `film_links[]`,
  `film_url`, `starting_open_url`, `starting_command`; первая ссылка — VK,
  вторая — Rutube. `with_starting_open_url = 146`, `with_film_links = 146`,
  `unique_starting_timecodes = 146`.
- `GET /api/admin/timecodes`: `moderation_links[]` содержит только VK/Rutube и локальную команду.
- `GET /api/admin/timecode-library`: `moderation.sources = ["vk_mosfilm","rutube_mosfilm","rutube_russia1"]`,
  14 `targets[]` с Rutube deep link.
- `GET /api/admin/moderation/love-confession/table`: 50 строк, `film_url` — точная
  прямая ссылка или `""`; поля `film_link_kind` (`direct` / `empty`), редактируемые
  таймкоды и `version_note`. Поисковых URL в ответе нет.
- `#/admin`: в карточке явно написано «источники: VK + Rutube» и «только VK/Rutube»;
  выпадающий список сервисов теперь `vkvideo` / `rutube`.
- `watch_links()` в пользовательском API тоже отдаёт только VK Видео и Rutube.

## Пополнение реестра

```bash
.venv/bin/python scripts/resolve_official_videos.py \
  --url "https://rutube.ru/video/2d0899d6388926b7caa66a09e9073572/" \
  --key "Ирония судьбы, или С лёгким паром!|1975" \
  --imdb-id tt0073179
```

Скрипт больше не ищет YouTube через `yt-dlp`: он принимает уже найденную
оператором официальную VK/Rutube-ссылку, достаёт `video_id` и печатает черновик.
Автоматического импорта нет; строку в JSON добавляют только после ручной проверки.

## Проверки и юридические оговорки

- `117 passed` pytest, `14 passed` Node/jsdom; `bash scripts/check.sh` с живым
  сервером — 5/5, `SMOKE OK`, `ВСЁ ЗЕЛЁНО ✅`.
- Тесты проверяют нормализацию `?t=` только для VK/Rutube, порядок VK→Rutube,
  отсутствие YouTube/Кинопоиска/ivi/Okko/IMDb в ссылках, 14 deep links,
  локальную команду и ручное подтверждение с `version_note`.
- Бесплатная официальная публикация ≠ общественное достояние. Перед продуктовым
  использованием нужна юридическая проверка конкретных ссылок.
- Мы не встраиваем плеер, не скачиваем видео, не храним тексты субтитров и не
  генерируем ссылки на нелегальные зеркала. `verified_by_human` остаётся `false`.

Связанные документы: [13-timecode-verification.md](13-timecode-verification.md),
[16-moderation-board.md](16-moderation-board.md),
[17-intent-taxonomy.md](17-intent-taxonomy.md),
[18-curator-workflow.md](18-curator-workflow.md).
