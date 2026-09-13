//! Answer-type classifier + `invention_score` lookup table (T045), ported
//! line-by-line from `product/ml/semantic/rules.py` — THAT module is the
//! definition; this one must be parity-tested against it (see
//! `product/ml/semantic/tests/test_rules.py` and this module's own tests
//! below, mirroring the same cases). Do not change the rule priority order
//! or the lookup table here without updating `rules.py` first.
//!
//! Deliberately dependency-free (no `regex`, no `unicode-normalization`):
//! `rules.py`'s `normalize()` runs full Unicode NFKD + combining-mark
//! stripping, but the only accented characters this pipeline ever actually
//! sees are Spanish ASR output (á é í ó ú ñ ü, upper/lower) — `fold_accents`
//! below special-cases exactly that set rather than pulling in a general
//! Unicode normalization crate for one alphabet's worth of diacritics.
//!
//! Rules operate on the caller's answer to the T043 probe question (the
//! first caller turn after the detected probe turn), checked in this
//! priority order (first match wins):
//!
//! ```text
//! 1. question_back      caller asks back instead of answering
//! 2. denial              caller states they don't have/know the thing
//! 3. hedge               caller is unsure / qualifies the answer
//! 4. assertion_numeric   answer contains >= 3 digit characters
//! 5. assertion_product   answer names one of the two offered products
//! 6. assertion_name      answer states a personal name
//! 7. other               none of the above (fallback)
//! ```

/// One of the seven answer-type categories (§ module docstring priority
/// order). `Other` doubles as both "no rule matched" and "empty/whitespace
/// text", matching `rules.py::classify_answer_type`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AnswerType {
    QuestionBack,
    Denial,
    Hedge,
    AssertionNumeric,
    AssertionProduct,
    AssertionName,
    Other,
}

impl AnswerType {
    /// `rules.py::invention_score` — fixed lookup, confident assertions
    /// score high, denial/question_back score low, hedge in between.
    pub fn invention_score(self) -> f64 {
        match self {
            AnswerType::AssertionNumeric => 0.90,
            AnswerType::AssertionProduct => 0.85,
            AnswerType::AssertionName => 0.80,
            AnswerType::Other => 0.50,
            AnswerType::Hedge => 0.35,
            AnswerType::QuestionBack => 0.15,
            AnswerType::Denial => 0.05,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            AnswerType::QuestionBack => "question_back",
            AnswerType::Denial => "denial",
            AnswerType::Hedge => "hedge",
            AnswerType::AssertionNumeric => "assertion_numeric",
            AnswerType::AssertionProduct => "assertion_product",
            AnswerType::AssertionName => "assertion_name",
            AnswerType::Other => "other",
        }
    }
}

// --------------------------------------------------------------------------
// Text normalisation (mirrors rules.py::normalize for the Spanish alphabet)
// --------------------------------------------------------------------------

fn fold_accents(c: char) -> char {
    match c {
        'á' | 'à' | 'ä' | 'â' | 'Á' | 'À' | 'Ä' | 'Â' => 'a',
        'é' | 'è' | 'ë' | 'ê' | 'É' | 'È' | 'Ë' | 'Ê' => 'e',
        'í' | 'ì' | 'ï' | 'î' | 'Í' | 'Ì' | 'Ï' | 'Î' => 'i',
        'ó' | 'ò' | 'ö' | 'ô' | 'Ó' | 'Ò' | 'Ö' | 'Ô' => 'o',
        'ú' | 'ù' | 'ü' | 'û' | 'Ú' | 'Ù' | 'Ü' | 'Û' => 'u',
        'ñ' | 'Ñ' => 'n',
        other => other,
    }
}

/// lowercase, strip accents/punctuation (keep digits), collapse whitespace.
pub fn normalize(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut last_was_space = true; // suppress leading spaces
    for c in text.chars() {
        let c = fold_accents(c.to_ascii_lowercase());
        // `to_ascii_lowercase` only folds ASCII; accented uppercase is
        // handled directly by `fold_accents`'s own upper/lower arms above.
        let mapped = if c.is_ascii_lowercase() || c.is_ascii_digit() {
            c
        } else {
            ' '
        };
        if mapped == ' ' {
            if !last_was_space {
                out.push(' ');
            }
            last_was_space = true;
        } else {
            out.push(mapped);
            last_was_space = false;
        }
    }
    while out.ends_with(' ') {
        out.pop();
    }
    out
}

// --------------------------------------------------------------------------
// Pattern tables (checked as substrings of the normalized text)
// --------------------------------------------------------------------------

const QUESTION_BACK_PATTERNS: &[&str] = &[
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
];

const DENIAL_PATTERNS: &[&str] = &[
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
];

const HEDGE_PATTERNS: &[&str] = &[
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
];

const PRODUCT_PATTERNS: &[&str] = &[
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
];

const NAME_INTROS: &[&str] = &["me llamo", "mi nombre es", "soy"];

fn contains_any(normalized: &str, patterns: &[&str]) -> bool {
    patterns.iter().any(|p| normalized.contains(p))
}

/// `rules.py::_NAME_INTRO_RE`: `\b(me llamo|mi nombre es|soy)\b\s+([A-Z...][a-z...]+)`
/// against the RAW (un-normalized) text — an intro phrase (case-insensitive,
/// whole-word) immediately followed by whitespace and a capitalized word.
fn contains_name_intro(text: &str) -> bool {
    let chars: Vec<char> = text.chars().collect();
    let lower: String = chars.iter().map(|c| c.to_ascii_lowercase()).collect();
    let lower_chars: Vec<char> = lower.chars().collect();

    let is_word_char = |c: char| c.is_alphanumeric() || c == '_';

    for intro in NAME_INTROS {
        let intro_chars: Vec<char> = intro.chars().collect();
        let n = intro_chars.len();
        if n > lower_chars.len() {
            continue;
        }
        for start in 0..=(lower_chars.len() - n) {
            if lower_chars[start..start + n] != intro_chars[..] {
                continue;
            }
            // whole-word: char before start (if any) and char after the
            // match (if any, before whitespace) must not be word chars.
            let before_ok = start == 0 || !is_word_char(lower_chars[start - 1]);
            let end = start + n;
            let after_ok = end == lower_chars.len() || !is_word_char(lower_chars[end]);
            if !before_ok || !after_ok {
                continue;
            }
            // skip one-or-more whitespace
            let mut i = end;
            let mut saw_space = false;
            while i < chars.len() && chars[i].is_whitespace() {
                i += 1;
                saw_space = true;
            }
            if !saw_space || i >= chars.len() {
                continue;
            }
            // first char of the following word must be uppercase, and the
            // word must contain at least one lowercase letter after it
            // (mirrors `[A-Z...][a-z...]+`: one or more lowercase letters).
            let first = chars[i];
            if !first.is_uppercase() {
                continue;
            }
            if i + 1 < chars.len() && chars[i + 1].is_lowercase() {
                return true;
            }
        }
    }
    false
}

fn count_digits(text: &str) -> usize {
    text.chars().filter(|c| c.is_ascii_digit()).count()
}

/// `rules.py::classify_answer_type` — one of [`AnswerType`] for the
/// caller's raw (un-normalized) answer `text`.
pub fn classify_answer_type(text: &str) -> AnswerType {
    if text.trim().is_empty() {
        return AnswerType::Other;
    }

    let norm = normalize(text);

    if contains_any(&norm, QUESTION_BACK_PATTERNS) {
        return AnswerType::QuestionBack;
    }
    if contains_any(&norm, DENIAL_PATTERNS) {
        return AnswerType::Denial;
    }
    if contains_any(&norm, HEDGE_PATTERNS) {
        return AnswerType::Hedge;
    }
    if count_digits(text) >= 3 {
        return AnswerType::AssertionNumeric;
    }
    if contains_any(&norm, PRODUCT_PATTERNS) {
        return AnswerType::AssertionProduct;
    }
    if contains_name_intro(text) {
        return AnswerType::AssertionName;
    }
    AnswerType::Other
}

/// `rules.py::analyze_answer`'s core (`word_count`/`response_latency` are
/// caller concerns in this port, not part of the parity-tested surface).
pub fn analyze_answer(text: &str) -> (AnswerType, f64) {
    let answer_type = classify_answer_type(text);
    (answer_type, answer_type.invention_score())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_or_whitespace_is_other() {
        assert_eq!(classify_answer_type(""), AnswerType::Other);
        assert_eq!(classify_answer_type("   "), AnswerType::Other);
    }

    #[test]
    fn question_back_matches() {
        assert_eq!(classify_answer_type("¿Cuál es?"), AnswerType::QuestionBack);
        assert_eq!(classify_answer_type("No entiendo la pregunta"), AnswerType::QuestionBack);
        assert_eq!(classify_answer_type("¿Podría repetir por favor?"), AnswerType::QuestionBack);
    }

    #[test]
    fn denial_matches() {
        assert_eq!(classify_answer_type("No tengo esa cuenta"), AnswerType::Denial);
        assert_eq!(classify_answer_type("No sé de qué me habla"), AnswerType::Denial);
        assert_eq!(classify_answer_type("Ninguna de las dos"), AnswerType::Denial);
    }

    #[test]
    fn hedge_matches() {
        assert_eq!(classify_answer_type("Creo que es la de nómina"), AnswerType::Hedge);
        assert_eq!(classify_answer_type("No estoy segura, tal vez"), AnswerType::Hedge);
    }

    #[test]
    fn assertion_numeric_needs_at_least_three_digits() {
        assert_eq!(classify_answer_type("Es la cuenta 12"), AnswerType::Other);
        assert_eq!(classify_answer_type("Es la cuenta 1234"), AnswerType::AssertionNumeric);
    }

    #[test]
    fn assertion_product_matches_spelling_variants() {
        assert_eq!(classify_answer_type("Es la de crédito verde"), AnswerType::AssertionProduct);
        assert_eq!(classify_answer_type("Sí, la no mina plus"), AnswerType::AssertionProduct);
    }

    #[test]
    fn assertion_name_matches_intro_plus_capitalized_word() {
        assert_eq!(classify_answer_type("Me llamo Roberto"), AnswerType::AssertionName);
        assert_eq!(classify_answer_type("Mi nombre es Ana"), AnswerType::AssertionName);
        assert_eq!(classify_answer_type("Soy Carlos"), AnswerType::AssertionName);
    }

    #[test]
    fn assertion_name_requires_capitalized_word_immediately_after() {
        // lowercase word after the intro should NOT count as a name.
        assert_eq!(classify_answer_type("soy yo"), AnswerType::Other);
    }

    #[test]
    fn other_is_the_fallback() {
        assert_eq!(classify_answer_type("Buenos días, gracias"), AnswerType::Other);
    }

    #[test]
    fn priority_order_question_back_beats_denial() {
        // Contains both a denial-ish and question-back-ish substring;
        // question_back must win since it's checked first.
        assert_eq!(classify_answer_type("No entiendo, no tengo idea"), AnswerType::QuestionBack);
    }

    #[test]
    fn invention_score_lookup_matches_the_frozen_table() {
        assert_eq!(AnswerType::AssertionNumeric.invention_score(), 0.90);
        assert_eq!(AnswerType::AssertionProduct.invention_score(), 0.85);
        assert_eq!(AnswerType::AssertionName.invention_score(), 0.80);
        assert_eq!(AnswerType::Other.invention_score(), 0.50);
        assert_eq!(AnswerType::Hedge.invention_score(), 0.35);
        assert_eq!(AnswerType::QuestionBack.invention_score(), 0.15);
        assert_eq!(AnswerType::Denial.invention_score(), 0.05);
    }

    #[test]
    fn normalize_strips_accents_punctuation_and_collapses_whitespace() {
        assert_eq!(normalize("¡Crédito   Verde!"), "credito verde");
        assert_eq!(normalize("Nómina  Plus?"), "nomina plus");
    }

    #[test]
    fn as_str_round_trips_all_variants() {
        for (t, s) in [
            (AnswerType::QuestionBack, "question_back"),
            (AnswerType::Denial, "denial"),
            (AnswerType::Hedge, "hedge"),
            (AnswerType::AssertionNumeric, "assertion_numeric"),
            (AnswerType::AssertionProduct, "assertion_product"),
            (AnswerType::AssertionName, "assertion_name"),
            (AnswerType::Other, "other"),
        ] {
            assert_eq!(t.as_str(), s);
        }
    }
}
