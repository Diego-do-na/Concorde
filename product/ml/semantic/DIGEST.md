# Semantic layer digest (T042)

Base model transcripts + probe ground truth over 353 calls with a cached base-model transcript: probe phrase found in 277 (78.5%) calls; a caller answer turn was identifiable for 276 (78.2%) of them.

## Rule table

| answer_type | matched when (priority order, first match wins) | invention_score |
|---|---|---|
| question_back | caller asks back ("¿cuál es?", "no entiendo", "¿podría repetir?") | 0.15 |
| denial | caller states absence ("no tengo", "no cuento con", "no sé", "no existe", "no me aparece", "ninguna") | 0.05 |
| hedge | caller qualifies the answer ("creo que", "no estoy seguro", "me imagino", "más o menos", "tal vez") | 0.35 |
| assertion_numeric | answer contains >= 3 digit characters | 0.90 |
| assertion_product | answer names one of the two offered products ("nómina plus" / "crédito verde", incl. ASR spelling variants) | 0.85 |
| assertion_name | answer states a personal name ("me llamo…", "mi nombre es…", "soy…" + capitalized word) | 0.80 |
| other | none of the above | 0.50 |


**Data-quality note**: 158/276 (57.2%) identified answer turns transcribed as *empty text* -- "the first caller turn after the probe" (§ T043's fixed definition) is often a brief interjection/breath with no ASR-recognisable speech, not the caller's substantive reply. These are classified `other` (word_count=0) below, which dominates that bucket -- this is a data-quality fact about the ground truth's turn-selection rule, not a gap in the rule table itself.

## answer_type distribution (all calls with an identified answer)

| answer_type | count | share |
|---|---|---|
| question_back | 3 | 1.1% |
| denial | 16 | 5.8% |
| hedge | 4 | 1.4% |
| assertion_numeric | 31 | 11.2% |
| assertion_product | 35 | 12.7% |
| assertion_name | 0 | 0.0% |
| other | 187 | 67.8% |

## answer_type distribution by manifest label -- the first honest look at whether F-23 carries signal

If `invention_score` carries no signal, its mean should land close to identical for `human` and `synthetic` calls; a gap is evidence the rule table is picking up a real behavioral difference, not noise.

| label | n | question_back | denial | hedge | assertion_numeric | assertion_product | assertion_name | other | mean invention_score |
|---|---|---|---|---|---|---|---|---|---|
| human | 124 | 1 (1%) | 2 (2%) | 0 (0%) | 10 (8%) | 6 (5%) | 0 (0%) | 105 (85%) | 0.539 |
| synthetic | 152 | 2 (1%) | 14 (9%) | 4 (3%) | 21 (14%) | 29 (19%) | 0 (0%) | 82 (54%) | 0.572 |

Mean invention_score gap (synthetic - human): **+0.033** (0.572 vs 0.539). Directionally consistent with the thesis (a scripted responder invents more than a human who can say "I don't have that") -- worth carrying into fc-2 as an optional F-23 candidate, pending a larger sample.
