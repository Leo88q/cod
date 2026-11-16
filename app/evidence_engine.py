"""Доказательный отбор таймкодов: момент публикуется без человека только если
его подтверждают независимые издания субтитров.

ПОЧЕМУ ТАК. Прежний скоринг сравнивал TF-IDF окна 25–75 сек (4–8 слов) с
описанием интента. На такой длине косинус почти нулевой (медиана 0.004), порог
0.45 набирался за счёт «2 совпавших слова + нормальная длина окна», поэтому
лучшим объявлялось случайное окно: медианный зазор top1/top2 — 0.001, до 151
кандидата на сцену. Ручная сверка была не решением, а неизбежным следствием.

Здесь вместо «магического скора» — три проверяемых факта:

1. **Якорь.** Лучшее окно сцены по интенту (лексикон с весами редкости + TF-IDF
   по всему эпизоду, а не по окну).
2. **Консенсус по содержанию.** Якорь ищется в ДРУГИХ изданиях субтитров не по
   абсолютной секунде, а по последовательности реплик (шинглы): издания сдвинуты
   на минуты, но порядок реплик сохраняется. Совпадение в 2 независимых файлах
   сверх одного — доказательство.
3. **Достаточность.** Окно проходит gate по плотности лексикона и не содержит
   негативных маркеров (титры, трейлер, реклама).

Результат — `Evidence` с полем `verdict`:
  * `auto`    — доказано (consensus >= min_editions), можно публиковать;
  * `review`  — есть кандидат, но издание одно или свидетельства слабые;
  * `none`    — доказательного фрагмента нет, сцена остаётся без таймкода.

Текст субтитров НЕ покидает этот модуль и не сохраняется: наружу уходят только
секунды, sha1 окна и агрегированные признаки.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field, asdict

# --- параметры gate (вынесены, чтобы их можно было менять и тестировать) -----
MIN_CLIP = 25.0
MAX_CLIP = 75.0
SKIP_HEAD = 120.0          # титры/студии в начале
SKIP_TAIL = 150.0          # финальные титры
MIN_GAP = 60.0             # разные кандидаты не ближе 60 сек друг к другу
CONSENSUS_WINDOW = 10.0    # ±10 сек внутри одного издания (без выравнивания)
SHINGLE = 4                # длина последовательности реплик для выравнивания

# Минимальная плотность «редких» лексиконных совпадений в окне.
# Вес слова = idf; редкое слово («признаюсь») весит в разы больше «любовь».
MIN_EVIDENCE = 1.6
NEGATIVE_MARKERS = (
    "subtitles by", "subtitle by", "www.", "http", "previously on", "trailer",
    "official trailer", "титры", "реклама", "трейлер", "продолжение следует",
    "music playing", "♪", "♫", "subs by", "opensubtitles", "сопровождение",
)

_WORD_RE = re.compile(r"[^\w\s'’-]+", re.UNICODE)


# --------------------------------------------------------------------------
# лексикон: веса редкости
# --------------------------------------------------------------------------
def tokenize(text: str, lang: str = "en") -> list[str]:
    """Нормализация: нижний регистр, без пунктуации, RU — грубый стемминг.

    Стемминг согласован с scripts/fetch_timecodes.norm, чтобы лексикон
    («признаюсь», «влюблён») совпадал с формой в реплике.
    """
    low = _WORD_RE.sub(" ", text.lower())
    words = [w for w in low.split() if len(w) > 1]
    if lang == "ru":
        words = [w[:5] if len(w) >= 6 else w for w in words]
    return words


@dataclass(frozen=True)
class Lexicon:
    """Лексикон интента с весами редкости.

    `idf` — из корпуса субтитров (log((N+1)/(df+1))+1). У «любви» idf ~1.0,
    у «признаюсь» — 5+. Именно это мешает общим словам раздувать скор.
    """

    words: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_terms(cls, terms_ru: list[str], terms_en: list[str]) -> "Lexicon":
        """Веса по умолчанию: чем длиннее термин, тем он специфичнее.

        Длина — дешёвый суррогат редкости: «любовь» (6) vs «ты мне нужен» (12).
        Как только есть корпус, используйте `with_corpus_idf`.
        """
        out: dict[str, float] = {}
        for term in list(terms_ru) + list(terms_en):
            t = " ".join(tokenize(term))
            if not t:
                continue
            base = 1.0 + 0.35 * len(t.split()) + 0.08 * max(0, len(t) - 4)
            out[t] = round(min(4.0, base), 3)
        return cls(out)

    def with_corpus_idf(self, idf: dict[str, float]) -> "Lexicon":
        """Уточнить веса реальной частотой из корпуса субтитров."""
        words = {}
        for term, default in self.words.items():
            parts = term.split()
            if len(parts) == 1:
                words[term] = round(idf.get(parts[0], default), 3)
            else:
                # фраза весит как сумма частей: «stay with me» сильнее «stay»
                words[term] = round(sum(idf.get(p, 1.5) for p in parts), 3)
        return Lexicon(words)

    def matches(self, text: str) -> tuple[float, int, list[str]]:
        """-> (вес совпадений, число совпадений, совпавшие термины).

        Учитываются отдельные слова и фразы. Фраза ищется как ПОСЛЕДОВАТЕЛЬНОСТЬ
        нормализованных токенов — иначе «stay with me» не совпал бы с «stay
        with me?» после стемминга. Текст никуда не возвращается.
        """
        flat = " ".join(tokenize(text)).split()
        if not flat:
            return 0.0, 0, []
        flat_set = set(flat)
        bigrams = {f"{flat[i]} {flat[i + 1]}" for i in range(len(flat) - 1)} if len(flat) > 1 else set()
        weight = 0.0
        hits: list[str] = []
        for term, w in self.words.items():
            parts = term.split()
            if len(parts) == 1:
                ok = term in flat_set
            elif len(parts) == 2:
                ok = term in bigrams
            elif len(term) < len(" ".join(flat)) + 2:
                # длинная фраза: ищем подстроку, только если она физически может
                # уместиться в окне (иначе не генерируем срезы на каждое окно)
                ok = term in " ".join(flat)
            else:
                ok = False
            if ok:
                weight += w
                hits.append(term)
        return round(weight, 3), len(hits), sorted(hits)


# --------------------------------------------------------------------------
# окна и признаки
# --------------------------------------------------------------------------
@dataclass
class Window:
    line: int
    start: float
    end: float
    text: str
    lines: int = 1
    max_gap: float = 0.0
    avg_chars: float = 0.0
    _sh: set[str] | None = field(default=None, repr=False, compare=False)

    @property
    def duration(self) -> float:
        return self.end - self.start


def make_windows(blocks, min_clip: float = MIN_CLIP, max_clip: float = MAX_CLIP,
                 max_windows: int = 0) -> list[Window]:
    """Режем субтитры на окна, начиная с реплики.

    `blocks` — [(idx, start, end, text)] в порядке файла.
    """
    if not blocks:
        return []
    by_idx = {b[0]: b for b in blocks}
    total_end = max(b[2] for b in blocks)
    step = 1
    if max_windows and len(blocks) > max_windows:
        step = max(1, len(blocks) // max_windows)
    wins: list[Window] = []
    for pos in range(0, len(blocks), step):
        idx, start, end, text = blocks[pos]
        if start < SKIP_HEAD or start > total_end - SKIP_TAIL:
            continue
        parts = [text]
        win_end, prev_end, j = end, end, idx
        gaps: list[float] = []
        while idx in by_idx and j + 1 in by_idx and win_end - start < min_clip and win_end - start < max_clip:
            j += 1
            b = by_idx[j]
            gaps.append(max(0.0, b[1] - prev_end))
            parts.append(b[3])
            win_end = max(win_end, b[2])
            prev_end = win_end
        if not (min_clip <= win_end - start <= max_clip + 2):
            continue
        wins.append(Window(
            line=idx, start=start, end=win_end, text=" ".join(parts),
            lines=len(parts), max_gap=max(gaps) if gaps else 0.0,
            avg_chars=sum(len(p) for p in parts) / max(1, len(parts)),
        ))
    return wins


def negative_score(text: str) -> int:
    low = text.lower()
    return sum(1 for m in NEGATIVE_MARKERS if m in low)


def structural_score(w: Window, expected_fragment: str = "dialogue") -> float:
    """0..1: насколько окно похоже на цельный фрагмент (а не на обрывок)."""
    s = 0.5
    if MIN_CLIP <= w.duration <= MAX_CLIP:
        s += 0.2
    if w.lines >= 2 and w.max_gap <= 5:
        s += 0.15
    if expected_fragment == "monologue" and w.lines <= 4 and w.avg_chars > 45:
        s += 0.15
    elif expected_fragment == "dialogue" and w.lines >= 4:
        s += 0.15
    return round(max(0.0, min(1.0, s)), 3)


# --------------------------------------------------------------------------
# консенсус по содержанию (выравнивание изданий)
# --------------------------------------------------------------------------
def shingles(w: Window, size: int = SHINGLE) -> set[str]:
    """Последовательность нормализованных реплик как отпечаток окна.

    Именно это делает издания сравнимыми: секунды сдвинуты, слова — нет.
    Отпечаток кэшируется в окне: без кэша сравнение всех пар окон было
    квадратичным по стоимости нормализации.
    """
    if w._sh is not None and size == SHINGLE:
        return w._sh
    words = re.findall(r"[a-zа-яё']+", w.text.lower())
    if len(words) < size:
        sh = {" ".join(words)} if words else set()
    else:
        sh = {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}
    if size == SHINGLE:
        w._sh = sh
    return sh


def _match_ratio(a: set[str], b: set[str]) -> float:
    """Доля отпечатков меньшего множества, найденная в большем (0..1)."""
    if not a or not b:
        return 0.0
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return len(small & big) / len(small)


@dataclass
class Edition:
    """Одно издание субтитров: имя источника, язык и окна.

    Язык нужен, потому что отпечатки реплик сравнимы только внутри одного
    языка: RU-субтитры подтверждают RU-издание, EN — EN-издание. Издания одного
    фильма на разных языках — независимые линии доказательства (и именно они
    закрывают советское кино, где GitHub-зеркала дают только английский).
    """
    name: str
    lang: str
    windows: list["Window"]

    def __iter__(self):
        # обратная совместимость: распаковка как (name, windows)
        return iter((self.name, self.windows))


@dataclass
class EditionMatch:
    edition: str
    start: float
    end: float
    ratio: float
    skew: float          # насколько издание сдвинуто относительно якоря, сек


def align_across_editions(anchor: Window, others: list[tuple[str, list[Window]]],
                          min_ratio: float = 0.35) -> list[EditionMatch]:
    """Ищем якорь в других изданиях по содержанию, не по секундам.

    Возвращает лучшие совпадения (по одному на издание). Издание, где тот же
    фрагмент стоит на другой минуте, — это и есть независимое подтверждение.
    """
    a_sh = shingles(anchor)
    if not a_sh:
        return []
    out: list[EditionMatch] = []
    for name, wins in others:
        best = None
        for w in wins:
            sh = shingles(w)
            # дешёвый префильтр: если пересечение меньше порога, ratio заведомо ниже
            if len(sh & a_sh) < min_ratio * min(len(sh), len(a_sh)) - 1e-9 and sh:
                continue
            r = _match_ratio(a_sh, sh)
            if best is None or r > best.ratio:
                best = EditionMatch(name, w.start, w.end, round(r, 3), round(w.start - anchor.start, 1))
        if best and best.ratio >= min_ratio:
            out.append(best)
    return out


# --------------------------------------------------------------------------
# выбор якоря и вердикт
# --------------------------------------------------------------------------
@dataclass
class Candidate:
    scene: str
    year: int | None
    intent: str
    fragment_type: str
    anchor: Window
    score: float
    evidence: float
    hits: int
    terms: list[str]
    consensus: int
    editions: list[str]
    matches: list[EditionMatch]
    reason: str
    sha1: str
    verdict: str = "none"

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("anchor")
        d["start"] = int(round(self.anchor.start))
        d["end"] = int(round(self.anchor.end))
        d["duration"] = int(round(self.anchor.duration))
        return d


def score_window(w: Window, lex: Lexicon, expected_fragment: str) -> tuple[float, float, int, list[str], int]:
    """-> (score, evidence_weight, hits, terms, negative_markers)."""
    weight, hits, terms = lex.matches(w.text)
    neg = negative_score(w.text)
    struct = structural_score(w, expected_fragment)
    # evidence — плотность лексикона на минуту: короткое окно не выигрывает
    # только за счёт длины, а длинное — за счёт «воды».
    density = weight / max(1.0, w.duration / 60.0)
    score = round(density + 0.5 * struct - 1.5 * min(2, neg), 3)
    return score, round(weight, 3), hits, terms, neg


def select_scene(scene: str, year: int | None, intent: dict, lex: Lexicon,
                 editions: list[Edition],
                 min_editions: int = 2,
                 min_evidence: float = MIN_EVIDENCE,
                 min_ratio: float = 0.55,
                 min_hits: int = 2,
                 max_probe: int = 40,
                 lexicon_for=None,
                 min_intent_confidence: float = 0.0) -> Candidate | None:
    """Полный проход по одной сцене.

    `editions` — издания (`Edition`: имя, язык, окна). `lexicon_for(lang)`
    отдаёт лексикон нужного языка; по умолчанию используется `lex`.

    Порядок приоритетов — сначала ДОКАЗАТЕЛЬСТВО, потом скор: якорем становится
    не самое «громкое» окно, а окно, которое подтвердилось другим изданием.
    Топ-1 по лексикону часто оказывается случайной репликой, поэтому простой
    выбор top-1 терял бы подтверждаемые сцены.
    """
    expected = intent.get("fragment_type", "dialogue")
    lex_for = lexicon_for or (lambda _lang: lex)
    scored: list[tuple[float, float, int, list[str], str, str, Window]] = []
    for ed in editions:
        ed_lex = lex_for(ed.lang)
        for w in ed.windows:
            score, ev, hits, terms, neg = score_window(w, ed_lex, expected)
            if ev >= min_evidence and neg == 0:
                scored.append((score, ev, hits, terms, ed.name, ed.lang, w))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])

    # Пробуем якоря по убыванию скора и берём первый доказанный; если ни один
    # не подтвердился — лучший по скору уходит на ручную проверку.
    best = None
    for score, ev, hits, terms, name, lang, anchor in scored[:max_probe]:
        # подтверждают только издания того же языка (RU подтверждает RU)
        others = [(ed.name, ed.windows) for ed in editions
                  if ed.name != name and ed.lang == lang]
        matches = align_across_editions(anchor, others)
        strong = [m for m in matches if m.ratio >= min_ratio]
        rank = (len(strong), max((m.ratio for m in strong), default=0.0), score)
        if best is None or rank > best[0]:
            best = (rank, score, ev, hits, terms, name, lang, anchor, matches, strong)
        if len(strong) + 1 >= min_editions and ev >= min_evidence and hits >= min_hits:
            break

    (n_strong, _r, _s), score, ev, hits, terms, name, lang, anchor, matches, strong = best
    consensus = 1 + len(strong)
    editions_ok = [name] + [m.edition for m in strong]

    # Разметка сцены тоже часть доказательства. Если интент угадан правилом с
    # низкой уверенностью, момент может быть про другое («Мальчишник в Вегасе»
    # под интентом «память, война»), поэтому такая сцена не публикуется сама.
    intent_ok = float(intent.get("confidence", 1.0)) >= min_intent_confidence
    if consensus >= min_editions and hits >= min_hits and ev >= min_evidence and intent_ok:
        verdict = "auto"
    elif consensus >= min_editions or matches:
        verdict = "review"
    else:
        # Издание одно (или содержимое не подтвердилось) — нужен глаз человека.
        verdict = "review" if ev >= min_evidence * 1.5 else "none"
    matches = strong or matches

    # Насколько разные издания расходятся по времени: полезно оператору
    # (издание может быть другой длины), но НЕ влияет на вердикт.
    skews = [abs(m.skew) for m in matches]
    reason_bits = [
        f"{hits} совпадений по интенту (вес {ev:g}, язык {lang})",
        f"подтверждений {len(matches)} из {len(others)} других изданий",
    ]
    if skews:
        reason_bits.append(f"сдвиг изданий до {int(max(skews))} сек")
    if not skews:
        reason_bits.append("издание одно — авто-публикация невозможна")
    if not intent_ok:
        reason_bits.append(
            f"интент размечен правилом с уверенностью {intent.get('confidence', 0):.2f}"
            f" < {min_intent_confidence:g} — нужна проверка смысла")

    return Candidate(
        scene=scene, year=year, intent=intent.get("primary_intent", ""),
        fragment_type=expected, anchor=anchor, score=score, evidence=ev, hits=hits,
        terms=terms, consensus=consensus, editions=editions_ok, matches=matches,
        reason="; ".join(reason_bits),
        sha1=hashlib.sha1(" ".join(sorted(shingles(anchor))).encode("utf-8")).hexdigest(),
        verdict=verdict,
    )


# --------------------------------------------------------------------------
# корпус: частоты для весов редкости
# --------------------------------------------------------------------------
def corpus_idf(editions: list[tuple[str, list[Window]]], lang: str = "en") -> dict[str, float]:
    """idf по файлам (а не по окнам): редкое в корпусе слово получает больший вес."""
    docs = []
    for _name, wins in editions:
        vocab = set()
        for w in wins:
            vocab.update(tokenize(w.text, lang))
        docs.append(vocab)
    n = len(docs) or 1
    df: dict[str, int] = {}
    for vocab in docs:
        for word in vocab:
            df[word] = df.get(word, 0) + 1
    return {w: round(math.log((n + 1) / (c + 1)) + 1.0, 3) for w, c in df.items()}
