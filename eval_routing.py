"""Оценка качества семантического роутинга на золотом наборе.

Прогоняет tests/test_data/golden_routing.yaml через ЖИВОЙ эмбеддер из
EMBEDD_MODEL (сейчас BAAI/bge-m3). По умолчанию оценивается только
эмбеддинг-слой и его пороги; с флагом --llm добавляется полный каскад с
LLM-роутером (нужна поднятая Ollama). В STUB-режиме не работает: качество
семантики проверяется только на живом стеке, как и @live_only тесты.

Сравниваются два варианта кодирования:
  - с префиксами "query: "/"passage: " — текущее поведение
    embedding_router (наследие intfloat/multilingual-e5-small);
  - без префиксов — нативный режим bge-m3 (модель обучалась без них).

Запуск:
    venv/bin/python eval_routing.py              # эмбеддинг-слой + свип порога
    venv/bin/python eval_routing.py --llm        # полный каскад с LLM (нужна Ollama)
    venv/bin/python eval_routing.py --only epoz  # фильтр по id/тексту кейса

Выход:
    - таблица по кейсам: expected vs top-1 для обоих вариантов, скор, дельта,
      решение каскада с текущими порогами (ROUTING_THRESHOLD /
      ROUTING_AMBIGUITY_DELTA из конфига);
    - сводка accuracy и список ошибок;
    - свип THRESHOLD при фиксированной AMBIGUITY_DELTA — выбор порога под
      баланс «точность direct-решений / сколько уходит в LLM-слой»;
    - exit code = число ошибок на кейсах с expected != null (для CI).

Кейсы с expected=null (целевой агент исчез из реестра) в accuracy не идут,
но печатаются отдельно — видно, куда они уходят сейчас.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import yaml

from config import settings
from registry import AGENTS
from routing import embedding_router

ROOT = Path(__file__).resolve().parent
GOLDEN = ROOT / "tests" / "test_data" / "golden_routing.yaml"

THRESHOLD = settings.routing_threshold
DELTA = settings.routing_ambiguity_delta
LOW = settings.routing_low_threshold

model = embedding_router.model  # SentenceTransformer(settings.embedd_model)

SWEEP = (0.30, 0.32, 0.35, 0.38, 0.40, 0.42, 0.45,
         0.50, 0.55, 0.60, 0.69)


def load_cases() -> list[dict]:
    raw = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    if raw.get("version") != 1:
        raise SystemExit(f"{GOLDEN}: поддерживается только version=1")
    return raw["cases"]


def candidates() -> dict[str, object]:
    return {a.id: a for a in AGENTS.values() if a.enabled and a.routable}


def encode(texts: list[str], prefix: bool):
    texts = [f"query: {t}" if prefix else t for t in texts]
    return model.encode(texts, normalize_embeddings=True)


def encode_passages(descriptions: list[str], prefix: bool):
    texts = [f"passage: {d}" if prefix else d for d in descriptions]
    return model.encode(texts, normalize_embeddings=True)


def build_index(pool: dict[str, object], prefix: bool):
    """Векторы пассажей описаний только для переданных кандидатов.
    Значение — матрица (n_пассажей, dim): близость = максимум по пассажам
    (multi-vector, как в продовом EmbeddingIndex)."""
    return {aid: encode_passages(pool[aid].passage_texts, prefix)
            for aid in pool}


def _max_score(query_vec, vecs) -> float:
    arr = np.asarray(vecs)
    if arr.ndim == 1:
        return float(query_vec @ arr)
    return float((query_vec @ arr.T).max())


def route(query_vec, vectors, threshold, delta, low):
    scores = sorted(
        ((aid, _max_score(query_vec, vecs)) for aid, vecs in vectors.items()),
        key=lambda kv: kv[1], reverse=True)
    top1, top1_score = scores[0]
    top2_score = scores[1][1] if len(scores) > 1 else 0.0
    gap = top1_score - top2_score
    if top1_score < low:
        decision = "no_match"
    elif top1_score < threshold:
        decision = "fallback"
    elif gap <= delta:
        decision = "ambiguous"
    else:
        decision = "direct"
    return top1, top1_score, gap, decision, scores


def fallback_agent(pool: dict[str, object]) -> str:
    if settings.fallback_agent in pool:
        return settings.fallback_agent
    return sorted(pool)[0]


def cascade_with_llm(query, decision, top1, scores, pool):
    """Повторяет MasterRouter: direct берём сразу, no_match — сразу fallback
    без LLM. Ambiguous — все лидеры в пределах дельты, fallback — top-k по
    близости (ROUTING_LLM_SHORTLIST); ответ LLM валидируется множеством
    кандидатов, иначе — fallback."""
    from routing import llm_router

    if decision == "direct":
        return top1
    if decision == "no_match":
        return fallback_agent(pool)
    if decision == "ambiguous" and scores:
        best_score = scores[0][1]
        shortlist = [aid for aid, score in scores
                     if best_score - score <= DELTA]
    else:
        shortlist = [aid for aid, _ in scores[:settings.routing_llm_shortlist]]
    try:
        agent = llm_router.route(query, shortlist)
    except Exception as e:
        print(f"      LLM-роутер упал ({type(e).__name__}: {e}) -> fallback")
        agent = None
    if agent in pool:
        return agent
    return fallback_agent(pool)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", action="store_true",
                        help="доигрывать неоднозначные/fallback кейсы через LLM-роутер")
    parser.add_argument("--only", default="",
                        help="фильтр: подстрока id или текста кейса")
    args = parser.parse_args()

    cases = [c for c in load_cases()
             if args.only.lower() in c["id"].lower()
             or args.only.lower() in c["query"].lower()]
    pool = candidates()

    print(f"Эмбеддер: {settings.embedd_model}")
    print(f"Кандидаты (enabled+routable): {', '.join(sorted(pool))}")
    print(f"Пороги из конфига: ROUTING_LOW_THRESHOLD={LOW}, "
          f"ROUTING_THRESHOLD={THRESHOLD}, ROUTING_AMBIGUITY_DELTA={DELTA}")
    print(f"Кейсов после фильтра: {len(cases)}")
    print()

    vec_pref = build_index(pool, prefix=True)
    vec_plain = build_index(pool, prefix=False)
    q_pref = encode([c["query"] for c in cases], prefix=True)
    q_plain = encode([c["query"] for c in cases], prefix=False)

    errors_pref: list[tuple[str, str, str, float]] = []
    errors_plain: list[tuple[str, str, str, float]] = []
    countable = 0
    null_rows: list[tuple[str, str, float, str, str]] = []

    print(f"{'кейс':<12} {'ожид.':<10} {'pref->':<12} {'plain->':<12} "
          f"{'скор':>6} {'дельта':>6} {'решение':<10} {'итог':<6}  (LLM)")
    print("-" * 92)

    for i, case in enumerate(cases):
        cid, query, expected = case["id"], case["query"], case.get("expected")
        r1 = route(q_pref[i], vec_pref, THRESHOLD, DELTA, LOW)
        r2 = route(q_plain[i], vec_plain, THRESHOLD, DELTA, LOW)

        if expected is None:
            null_rows.append((cid, r1[0], r1[1], r1[3], r2[0]))
            print(f"{cid:<12} {'-':<10} {r1[0]:<12} {r2[0]:<12} "
                  f"{r1[1]:>6.3f} {r1[2]:>6.3f} {r1[3]:<10} {'-':<6}")
            continue

        countable += 1
        ok_pref = r1[0] == expected
        ok_plain = r2[0] == expected
        if not ok_pref:
            errors_pref.append((cid, expected, r1[0], r1[1]))
        if not ok_plain:
            errors_plain.append((cid, expected, r2[0], r2[1]))

        final = r1[0]
        llm_note = ""
        if args.llm and r1[3] not in ("direct", "no_match"):
            final = cascade_with_llm(query, r1[3], r1[0], r1[4], pool)
            llm_note = f"llm->{final}"
        elif args.llm and r1[3] == "no_match":
            llm_note = f"no_llm->{final}"
        elif args.llm:
            llm_note = "-"

        status = "OK" if ok_pref else "FAIL"
        print(f"{cid:<12} {expected:<10} {r1[0]:<12} {r2[0]:<12} "
              f"{r1[1]:>6.3f} {r1[2]:>6.3f} {r1[3]:<10} {status:<6}  {llm_note}")

    print()
    acc_pref = (countable - len(errors_pref)) / countable if countable else 0.0
    acc_plain = (countable - len(errors_plain)) / countable if countable else 0.0
    print(f"Top-1 accuracy (с префиксами, текущий режим): "
          f"{countable - len(errors_pref)}/{countable} = {acc_pref:.1%}")
    print(f"Top-1 accuracy (без префиксов, bge-m3 нативно):  "
          f"{countable - len(errors_plain)}/{countable} = {acc_plain:.1%}")

    for label, errs in (("с префиксами", errors_pref),
                        ("без префиксов", errors_plain)):
        if errs:
            print(f"\nОшибки ({label}):")
            for cid, exp, got, score in errs:
                print(f"  {cid:<12} {query_of(cid, cases)!r:<55} "
                      f"ожидали {exp}, ушло в {got} ({score:.3f})")

    if null_rows:
        print("\nКейсы без целевого агента в реестре (expected=null):")
        for cid, got, score, dec, got_plain in null_rows:
            print(f"  {cid:<12} {query_of(cid, cases)!r:<55} "
                  f"pref: {got} ({score:.3f}, {dec})  plain: {got_plain}")

    sweep(cases, vec_pref, countable)
    return min(len(errors_pref), 255)


def query_of(cid: str, cases: list[dict]) -> str:
    for c in cases:
        if c["id"] == cid:
            return c["query"]
    return "?"


def sweep(cases, vectors, countable):
    """Свип THRESHOLD при фиксированных DELTA/LOW по top-1 кейсов. Показывает,
    куда сдвигать порог под bge-m3: точность direct-решений vs доля запросов,
    уходящих в LLM-слой/fallback. no_match (ниже ROUTING_LOW_THRESHOLD, LLM не
    вызывается) входит в колонку fallb%. Текущий порог отмечен '*'."""
    print("\nСвип THRESHOLD (ROUTING_AMBIGUITY_DELTA и ROUTING_LOW_THRESHOLD "
          "фиксированы):")
    print(f"{'T':>5} {'direct%':>8} {'dir.точн.%':>10} {'ambigu%':>8} "
          f"{'fallb%':>7} {'top1-acc%':>10}")
    q = encode([c["query"] for c in cases], prefix=True)
    for t in SWEEP:
        n_direct = n_amb = n_fall = 0
        dir_correct = top1_correct = 0
        for i, case in enumerate(cases):
            if case.get("expected") is None:
                continue
            top1, score, gap, decision, _ = route(q[i], vectors, t, DELTA, LOW)
            if decision == "direct":
                n_direct += 1
                if top1 == case["expected"]:
                    dir_correct += 1
            elif decision == "ambiguous":
                n_amb += 1
            else:
                n_fall += 1
            if top1 == case["expected"]:
                top1_correct += 1
        mark = "*" if t == THRESHOLD else " "
        print(f"{t:>5.2f}{mark} {n_direct/countable:>7.0%} "
              f"{dir_correct/max(n_direct,1):>9.0%} {n_amb/countable:>7.0%} "
              f"{n_fall/countable:>6.0%} {top1_correct/countable:>9.0%}")


if __name__ == "__main__":
    sys.exit(main())
