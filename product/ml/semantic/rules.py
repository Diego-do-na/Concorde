#!/usr/bin/env python3
"""Answer-type classifier + invention_score lookup table (T042).

THIS MODULE IS THE DEFINITION. T045 ports these exact rules to Rust and
parity-tests its output against `classify_answer_type`/`invention_score`
here — do not change the rule order or the lookup table without updating
that parity test.

Deliberately stdlib-only (re, unicodedata) so it stays trivially portable
and parity-testable: no dependency on transcribe.py, numpy, pandas or the
dataset. The DIGEST.md-building code in `main()` imports those lazily,
only when actually generating the digest.

Rules operate on the caller's answer to the probe question (§ T043's
ground truth: the first caller turn after the agent's probe turn). Answer
types, checked in this priority order (first match wins):

    1. question_back      caller asks back instead of answering
    2. denial              caller states they don't have/know the thing
    3. hedge               caller is unsure / qualifies the answer
    4. assertion_numeric   answer contains >= 3 digit characters
    5. assertion_product   answer names one of the two offered products
    6. assertion_name      answer states a personal name
    7. other               none of the above (fallback)

`invention_score(answer_type)` is a fixed lookup (documented below and in
DIGEST.md): confident assertions score high (a human with no account under
discussion has nothing to confidently assert; a template-following system
tends to invent one), denial/question_back score low, hedge sits between.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional, TypedDict

# --------------------------------------------------------------------------
# Text normalisation (kept independent from transcribe.normalize_text on
# purpose -- this module must not import transcribe.py, see module docstring)
# --------------------------------------------------------------------------


def normalize(text: str) -> str:
    """lowercase, strip accents/punctuation (keep digits), collapse whitespace."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# Answer types
# --------------------------------------------------------------------------

ANSWER_TYPES = (
    "question_back",
    "denial",
    "hedge",
    "assertion_numeric",
    "assertion_product",
    "assertion_name",
    "other",
)

# Substrings checked against the *normalized* (accent/punctuation-stripped)
# text. Order within a list doesn't matter; order between categories (the
# priority list above) does.
_QUESTION_BACK_PATTERNS = (
    "cual es",
    "cuales son",
    "que es eso",
    "que es esto",
    "que es esa",
    "a que se refiere",
    "no entiendo",
    "podria repetir",
    "puede repetir",
    "como dice",
    "perdon cual",
    "disculpe cual",
)

_DENIAL_PATTERNS = (
    "no tengo",
    "no cuento con",
    "no se",
    "no lo se",
    "no existe",
    "no me aparece",
    "no manejo",
    "no cuento",
    "ninguna",
    "ningun",
    "no cual",
)

_HEDGE_PATTERNS = (
    "creo que",
    "creo qie",
    "no estoy segur",
    "no estoy muy segur",
    "me imagino",
    "mas o menos",
    "tal vez",
    "talvez",
    "quizas",
    "supongo",
    "posiblemente",
    "puede que",
)

# "nómina plus" / "crédito verde" plus the ASR spelling variants measured
# on this dataset (same variants as transcribe.PROBE_PHRASE_VARIANTS's
# "nomina" family, extended with more variants measured against caller
# answers specifically -- callers paraphrase/mishear the product names
# whisper already mangled in the agent's own probe turn).
_PRODUCT_PATTERNS = (
    "nomina plus",
    "no mina plus",
    "no mine plus",
    "no mine a plus",
    "no minea plus",
    "no me ena plus",
    "no me na plus",
    "no mi no plus",
    "nomina fluz",
    "nomina flus",
    "nominaplus",
    "credito verde",
    "credito berde",
    "creditoverde",
    "redito verde",
    "decreto verde",
    "creto verde",
)

_NAME_INTRO_RE = re.compile(
    r"\b(me llamo|mi nombre es|soy)\b\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)"
)

_DIGIT_RE = re.compile(r"\d")


def _contains_any(normalized_text: str, patterns: tuple) -> bool:
    return any(p in normalized_text for p in patterns)


def classify_answer_type(text: str) -> str:
    """One of ANSWER_TYPES for the caller's answer `text` (raw, un-normalized)."""
    if not text or not text.strip():
        return "other"

    norm = normalize(text)

    if _contains_any(norm, _QUESTION_BACK_PATTERNS):
        return "question_back"
    if _contains_any(norm, _DENIAL_PATTERNS):
        return "denial"
    if _contains_any(norm, _HEDGE_PATTERNS):
        return "hedge"
    if len(_DIGIT_RE.findall(text)) >= 3:
        return "assertion_numeric"
    if _contains_any(norm, _PRODUCT_PATTERNS):
        return "assertion_product"
    if _NAME_INTRO_RE.search(text):
        return "assertion_name"
    return "other"


def word_count(text: str) -> int:
    return len(text.split()) if text else 0


# Fixed lookup table (§ module docstring): assertion_* high (a confident
# claim about something that shouldn't exist is the invented-information
# tell Altur's brief describes), denial/question_back low (a human with
# nothing to report says so or asks what's being asked), hedge in between.
_INVENTION_SCORE = {
    "assertion_numeric": 0.90,
    "assertion_product": 0.85,
    "assertion_name": 0.80,
    "other": 0.50,
    "hedge": 0.35,
    "question_back": 0.15,
    "denial": 0.05,
}


def invention_score(answer_type: str) -> float:
    try:
        return _INVENTION_SCORE[answer_type]
    except KeyError as exc:
        raise ValueError(f"unknown answer_type: {answer_type!r}") from exc


class AnswerAnalysis(TypedDict):
    answer_type: str
    word_count: int
    response_latency: Optional[float]
    invention_score: float


def analyze_answer(text: str, response_latency: Optional[float] = None) -> AnswerAnalysis:
    """Combines classify_answer_type/word_count/invention_score into one record."""
    answer_type = classify_answer_type(text)
    return {
        "answer_type": answer_type,
        "word_count": word_count(text),
        "response_latency": response_latency,
        "invention_score": invention_score(answer_type),
    }


# --------------------------------------------------------------------------
# DIGEST.md generation (imports common/transcribe lazily -- see module docstring)
# --------------------------------------------------------------------------

RULE_TABLE_MD = """\
| answer_type | matched when (priority order, first match wins) | invention_score |
|---|---|---|
| question_back | caller asks back ("¿cuál es?", "no entiendo", "¿podría repetir?") | 0.15 |
| denial | caller states absence ("no tengo", "no cuento con", "no sé", "no existe", "no me aparece", "ninguna") | 0.05 |
| hedge | caller qualifies the answer ("creo que", "no estoy seguro", "me imagino", "más o menos", "tal vez") | 0.35 |
| assertion_numeric | answer contains >= 3 digit characters | 0.90 |
| assertion_product | answer names one of the two offered products ("nómina plus" / "crédito verde", incl. ASR spelling variants) | 0.85 |
| assertion_name | answer states a personal name ("me llamo…", "mi nombre es…", "soy…" + capitalized word) | 0.80 |
| other | none of the above | 0.50 |
"""


def _load_ground_truth_and_transcripts(semantic_dir, model: str = "base"):
    import json

    cache_dir = semantic_dir / "cache"
    gt_path = cache_dir / "probe_ground_truth.json"
    if not gt_path.is_file():
        raise FileNotFoundError(
            f"{gt_path} not found -- run transcribe.py first (it writes this file)."
        )
    with gt_path.open("r", encoding="utf-8") as f:
        gt = json.load(f)

    rows = []
    for anon_id, entry in gt["calls"].items():
        if not entry.get("probe_found") or entry.get("answer_turn_index") is None:
            continue
        transcript_path = cache_dir / "transcripts" / model / f"{anon_id}.json"
        if not transcript_path.is_file():
            continue
        with transcript_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        turns = data["turns"]
        answer_idx = entry["answer_turn_index"]
        if answer_idx >= len(turns):
            continue
        text = turns[answer_idx]["text"]
        analysis = analyze_answer(text, entry.get("response_latency"))
        analysis["is_empty"] = not text.strip()
        rows.append({"anon_id": anon_id, **analysis})
    return gt, rows


def build_digest(semantic_dir=None, model: str = "base") -> str:
    import sys
    from pathlib import Path

    if semantic_dir is None:
        semantic_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(semantic_dir.parent))
    from common.dataset import load_manifest  # noqa: E402

    gt, rows = _load_ground_truth_and_transcripts(semantic_dir, model=model)

    manifest = load_manifest()
    label_by_id = dict(zip(manifest["anon_id"], manifest["label"]))
    for row in rows:
        row["label"] = label_by_id.get(row["anon_id"])

    n_calls = gt["considered"]
    n_probe_found = gt["found"]
    n_answers = len(rows)

    counts = {t: 0 for t in ANSWER_TYPES}
    for row in rows:
        counts[row["answer_type"]] += 1

    by_label: dict = {}
    for label in ("human", "synthetic"):
        label_rows = [r for r in rows if r["label"] == label]
        n = len(label_rows)
        label_counts = {t: 0 for t in ANSWER_TYPES}
        for r in label_rows:
            label_counts[r["answer_type"]] += 1
        mean_score = (sum(r["invention_score"] for r in label_rows) / n) if n else 0.0
        by_label[label] = {"n": n, "counts": label_counts, "mean_invention_score": mean_score}

    lines = []
    lines.append("# Semantic layer digest (T042)\n")
    lines.append(
        f"Base model transcripts + probe ground truth over {n_calls} calls with a "
        f"cached base-model transcript: probe phrase found in {n_probe_found} "
        f"({n_probe_found / n_calls:.1%}) calls; a caller answer turn was "
        f"identifiable for {n_answers} ({n_answers / n_calls:.1%}) of them.\n"
    )
    lines.append("## Rule table\n")
    lines.append(RULE_TABLE_MD)

    n_empty = sum(1 for r in rows if r["is_empty"])
    lines.append(
        f"\n**Data-quality note**: {n_empty}/{n_answers} ({n_empty / n_answers:.1%}) "
        "identified answer turns transcribed as *empty text* -- \"the first caller "
        "turn after the probe\" (§ T043's fixed definition) is often a brief "
        "interjection/breath with no ASR-recognisable speech, not the caller's "
        "substantive reply. These are classified `other` (word_count=0) below, "
        "which dominates that bucket -- this is a data-quality fact about the "
        "ground truth's turn-selection rule, not a gap in the rule table itself."
    )

    lines.append("\n## answer_type distribution (all calls with an identified answer)\n")
    lines.append("| answer_type | count | share |")
    lines.append("|---|---|---|")
    for t in ANSWER_TYPES:
        share = (counts[t] / n_answers) if n_answers else 0.0
        lines.append(f"| {t} | {counts[t]} | {share:.1%} |")

    lines.append(
        "\n## answer_type distribution by manifest label -- the first honest look "
        "at whether F-23 carries signal\n"
    )
    lines.append(
        "If `invention_score` carries no signal, its mean should land close to "
        "identical for `human` and `synthetic` calls; a gap is evidence the rule "
        "table is picking up a real behavioral difference, not noise.\n"
    )
    lines.append("| label | n | " + " | ".join(ANSWER_TYPES) + " | mean invention_score |")
    lines.append("|---|---|" + "---|" * len(ANSWER_TYPES) + "---|")
    for label in ("human", "synthetic"):
        stats = by_label[label]
        n = stats["n"]
        cells = [
            f"{stats['counts'][t]} ({stats['counts'][t] / n:.0%})" if n else "0"
            for t in ANSWER_TYPES
        ]
        lines.append(
            f"| {label} | {n} | " + " | ".join(cells) + f" | {stats['mean_invention_score']:.3f} |"
        )

    human_mean = by_label["human"]["mean_invention_score"]
    synthetic_mean = by_label["synthetic"]["mean_invention_score"]
    gap = synthetic_mean - human_mean
    lines.append(
        f"\nMean invention_score gap (synthetic - human): **{gap:+.3f}** "
        f"({synthetic_mean:.3f} vs {human_mean:.3f}). "
        + (
            "Directionally consistent with the thesis (a scripted responder invents "
            "more than a human who can say \"I don't have that\") -- worth carrying "
            "into fc-2 as an optional F-23 candidate, pending a larger sample."
            if gap > 0.03
            else "No clear separation on this sample -- do not treat F-23 as a strong "
            "signal without further validation before adding it to a future contract."
        )
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    from pathlib import Path

    semantic_dir = Path(__file__).resolve().parent
    digest = build_digest(semantic_dir)
    out_path = semantic_dir / "DIGEST.md"
    out_path.write_text(digest, encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
