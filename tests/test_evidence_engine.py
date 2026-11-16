"""Доказательный отбор таймкодов: консенсус изданий вместо ручной сверки.

Тесты фиксируют то, ради чего движок написан:
  * общие слова («love», «time», «вместе») не проходят gate сами по себе;
  * фраза из лексикона весит больше, чем одно слово;
  * одно издание — не доказательство, публикации нет;
  * сборник субтитров без общего фрагмента не даёт ложного консенсуса;
  * одинаковый фрагмент на разных минутах разных изданий — доказательство.
"""
import random

from app import evidence_engine as E


def ed(name: str, wins: list, lang: str = "en") -> E.Edition:
    """Издание субтитров: имя, язык, окна."""
    return E.Edition(name, lang, wins)


def blocks(*rows):
    """[(idx, start, end, text)] — как отдаёт fetch_timecodes.parse_srt."""
    return [(i + 1, s, e, t) for i, (s, e, t) in enumerate(rows)]


def long_line(seed: int, words: int = 8) -> str:
    rnd = random.Random(seed)
    vocab = ["дорога", "поезд", "письмо", "мать", "небо", "город", "дверь", "окно",
             "утро", "вечер", "стол", "стакан", "улица", "снег", "ветер", "море"]
    return " ".join(rnd.choice(vocab) for _ in range(words))


def scene_blocks(anchor_text: str, anchor_at: float = 800.0, filler: str = "filler") -> list:
    """Синтетический фильм: 30 минут «воды» и один содержательный фрагмент."""
    rows = []
    t = 130.0
    idx = 0
    while t < anchor_at - 40:
        rows.append((t, t + 3, f"{filler} {long_line(idx)}"))
        t += 6.0
        idx += 1
    # сам фрагмент: 4 реплики по 8–9 сек
    for part in anchor_text.split("|"):
        rows.append((t, t + 9, part.strip()))
        t += 9.5
    while t < anchor_at + 900:
        rows.append((t, t + 3, f"{filler} {long_line(idx)}"))
        t += 6.0
        idx += 1
    return blocks(*rows)


LOVE = E.Lexicon.from_terms(
    ["люблю", "любовь", "признаюсь", "чувства", "сердце", "ты мне нужен"],
    ["love", "confess", "feelings", "my heart", "stay with me", "need you"],
)


def test_generic_words_alone_do_not_pass_gate():
    """Окно из общих слов не должно набирать evidence — иначе gate бессмысленный."""
    win = E.Window(line=1, start=800, end=860, lines=6, max_gap=0.5, avg_chars=20,
                   text="I love this time, we come together, my heart is a time machine")
    _score, evidence, hits, terms, neg = E.score_window(win, LOVE, "dialogue")
    assert hits >= 1, "общие слова должны находиться — вопрос в их весе"
    # одно-два общих слова не дают «доказательства» сопоставимого с фразой
    strong = E.Window(line=2, start=800, end=860, lines=6, max_gap=0.5, avg_chars=20,
                      text="I need you, stay with me, I confess my feelings")
    _s2, ev2, hits2, _t2, _n2 = E.score_window(strong, LOVE, "dialogue")
    assert ev2 > evidence * 2, "фраза + специфичные слова должны весить заметно больше"
    assert hits2 >= 3


def test_phrase_weighs_more_than_single_word():
    w_single, h1, _ = LOVE.matches("I love it")
    w_phrase, h2, terms = LOVE.matches("please stay with me")
    assert w_phrase > w_single and h2 == 1 and terms == ["stay with me"]
    assert h1 == 1


def test_negative_markers_are_rejected():
    win = E.Window(line=1, start=800, end=860, lines=5, max_gap=1, avg_chars=20,
                   text="I love you my heart ♪ music playing ♪ subtitles by www.example.com")
    _s, _ev, _h, _t, neg = E.score_window(win, LOVE, "dialogue")
    assert neg >= 2


def test_single_edition_never_auto_publishes():
    """Одно издание — это не доказательство, максимум ручная проверка."""
    wins = E.make_windows(scene_blocks("I love you|stay with me|my heart is yours|I confess"))
    cand = E.select_scene("Тест", 2000, {"primary_intent": "love_confession",
                                         "fragment_type": "dialogue"},
                          LOVE, [ed("edition-a", wins)])
    assert cand is not None
    assert cand.consensus == 1
    assert cand.verdict in ("review", "none")
    assert cand.verdict != "auto"


def test_two_editions_with_shifted_timing_confirm_each_other():
    """Издание сдвинуто на 3 минуты, но фрагмент совпадает -> это доказательство."""
    text = "I love you|stay with me|my heart is yours|I confess my feelings"
    a = E.make_windows(scene_blocks(text, anchor_at=800))
    b = E.make_windows(scene_blocks(text, anchor_at=980))
    cand = E.select_scene("Тест", 2000, {"primary_intent": "love_confession",
                                         "fragment_type": "dialogue"},
                          LOVE, [ed("a", a), ed("b", b)])
    assert cand is not None
    assert cand.verdict == "auto", cand.reason
    assert cand.consensus == 2
    assert cand.matches and abs(cand.matches[0].skew) > 100, "издание действительно сдвинуто"
    assert cand.matches[0].ratio >= 0.55


def test_unrelated_editions_do_not_produce_false_consensus():
    """Два разных фильма в одном наборе субтитров не должны «подтвердить» друг друга."""
    text = "I love you|stay with me|my heart is yours|I confess"
    a = E.make_windows(scene_blocks(text, anchor_at=800))
    rnd = random.Random(7)
    noise = blocks(*[(t, t + 3, " ".join(rnd.choice(["кот", "дом", "снег", "поле"])
                                        for _ in range(9)))
                     for t in range(130, 1700, 6)])
    b = E.make_windows(noise)
    cand = E.select_scene("Тест", 2000, {"primary_intent": "love_confession",
                                         "fragment_type": "dialogue"},
                          LOVE, [ed("a", a), ed("b", b)])
    assert cand is None or cand.verdict != "auto"


def test_alignment_finds_fragment_by_content_not_by_absolute_time():
    text = "I love you|stay with me|my heart is yours|I confess"
    anchor_wins = E.make_windows(scene_blocks(text, anchor_at=800))
    shifted_wins = E.make_windows(scene_blocks(text, anchor_at=1200))
    anchor = next(w for w in anchor_wins if "confess" in w.text.lower())
    matches = E.align_across_editions(anchor, [("shifted", shifted_wins)])
    assert matches and matches[0].ratio >= 0.55
    assert abs(matches[0].skew) > 300


def test_editions_in_different_languages_do_not_confirm_each_other():
    """RU-отпечатки несравнимы с EN: подтверждение — только внутри языка.

    Это важно для OPUS: русские субтитры и английские — две независимые линии,
    и «подтверждение» между ними было бы фикцией.
    """
    text = "I love you|stay with me|my heart is yours|I confess"
    a = E.make_windows(scene_blocks(text, anchor_at=800))
    b = E.make_windows(scene_blocks(text, anchor_at=980))
    cand = E.select_scene("Тест", 2000, {"primary_intent": "love_confession",
                                         "fragment_type": "dialogue"},
                          LOVE, [ed("en-1", a, "en"), ed("ru-1", b, "ru")])
    assert cand is not None
    assert candid_not_auto(cand), "межъязыковое совпадение не доказательство"
    cand2 = E.select_scene("Тест", 2000, {"primary_intent": "love_confession",
                                          "fragment_type": "dialogue"},
                           LOVE, [ed("ru-1", a, "ru"), ed("ru-2", b, "ru")])
    assert cand2 is not None and cand2.verdict == "auto"


def candid_not_auto(cand) -> bool:
    return cand.verdict != "auto"


def test_windows_respect_min_and_max_clip():
    wins = E.make_windows(scene_blocks("I love you|stay with me|my heart|I confess"))
    assert all(E.MIN_CLIP - 1 <= w.duration <= E.MAX_CLIP + 2 for w in wins)


def test_head_and_tail_are_skipped():
    """Титры и студии в начале и финальные титры не считаются сценой."""
    rows = [(5, 8, "I love you, stay with me"), (20, 23, "my heart")]
    wins = E.make_windows(blocks(*rows))
    assert wins == [] or all(w.start >= E.SKIP_HEAD for w in wins)


def test_corpus_idf_makes_common_words_cheap():
    """idf считается по корпусу: слово в каждом файле дешевле редкого.

    Внутри одной сцены (2 издания) idf бесполезен — все слова есть в обоих
    файлах; поэтому движок считает idf по всему корпусу.
    """
    common = E.make_windows(scene_blocks("I love you|my heart|love", anchor_at=800))
    corpus = [("a", common), ("b", common),
              ("c", E.make_windows(scene_blocks("I confess my feelings|stay with me|confess",
                                                anchor_at=800)))]
    idf = E.corpus_idf(corpus, "en")
    assert idf["love"] < idf["confess"], "частотное слово должно весить меньше редкого"
    # внутри одной пары файлов idf вырождается — это и есть причина считать его глобально
    pair_idf = E.corpus_idf([("a", common), ("b", common)], "en")
    assert pair_idf["love"] == pair_idf["heart"], "в паре файлов веса равны — редкость не измеряется"


def test_select_is_deterministic():
    text = "I love you|stay with me|my heart|I confess"
    editions = [ed("a", E.make_windows(scene_blocks(text, anchor_at=800))),
                ed("b", E.make_windows(scene_blocks(text, anchor_at=980)))]
    intent = {"primary_intent": "love_confession", "fragment_type": "dialogue"}
    one = E.select_scene("Тест", 2000, intent, LOVE, editions)
    two = E.select_scene("Тест", 2000, intent, LOVE, editions)
    assert (one.anchor.start, one.verdict, one.consensus) == (two.anchor.start, two.verdict, two.consensus)
