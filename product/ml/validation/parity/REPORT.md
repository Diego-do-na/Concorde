# Golden-vector parity report (FR-005, T015)

Python reference extractor (`ml/features/extract.py`) vs. Rust production
extractor (`api/src/features/`), on the 13 fixed fixtures in
`ml/validation/parity/golden/` (5 human + 5 synthetic calls from `train`,
plus 3 hand-built degenerate cases: no caller turns, one turn per channel,
fully-overlapping turns). Measured by `cargo test --test parity`
(`api/tests/parity.rs`), tolerance `1e-6` element-wise.

**Result: PASS.** All 13 fixtures match within tolerance; max abs error
across every feature is many orders of magnitude below the `1e-6` budget —
what's left is pure f64 arithmetic-order noise (machine epsilon, ~1e-16),
not a behavioral disagreement between the two extractors.

Golden fixtures store turn boundaries and `duration_s` rounded to `f32`
precision before either extractor runs (see `make_golden.py::_f32_round_trip`).
This matters: `features::extract` takes `f32` turn boundaries (mirroring
real VAD output at serving time), so comparing it against a Python run on
the dataset's full f64 precision would measure f32-promotion rounding, not
extractor disagreement, and would spuriously exceed 1e-6 on any real call
with many turns. Rounding both extractors' *inputs* to the same f32 values
first is what makes the 1e-6 output tolerance a meaningful parity check.

## Max abs error per feature (across all 13 fixtures)

| Feature | Max abs error |
|---|---|
| `resp_latency_mean` | 0 |
| `resp_latency_median` | 0 |
| `resp_latency_std` | 8.881784197001252e-16 |
| `resp_latency_cv` | 2.220446049250313e-16 |
| `latency_monotony_index` | 2.7755575615628914e-17 |
| `resp_latency_min` | 0 |
| `overlap_count` | 0 |
| `overlap_rate_per_min` | 4.440892098500626e-16 |
| `overlap_total_dur` | 0 |
| `caller_bargein_count` | 0 |
| `recovery_delay_mean` | 1.7763568394002505e-15 |
| `recovery_delay_cv` | 0 |
| `recovery_abort_ratio` | 2.7755575615628914e-17 |
| `caller_turn_dur_mean` | 2.220446049250313e-16 |
| `caller_turn_dur_std` | 0 |
| `caller_turn_dur_cv` | 2.220446049250313e-16 |
| `short_turn_ratio` | 0 |
| `fragmentation_rate` | 5.551115123125783e-17 |
| `caller_speech_ratio` | 2.7755575615628914e-17 |
| `speech_balance` | 0 |
| `silence_break_delay_mean` | 1.7763568394002505e-15 |
| `silence_break_delay_cv` | 0 |
| `turn_count_caller` | 0 |

## Reproducing

```bash
# from product/ml
python validation/parity/make_golden.py   # regenerate golden/ (idempotent; needs CONCORDE_DATASET_DIR)

# from product/api
cargo test --test parity                  # pass/fail + first mismatching feature on failure
```

## Fixtures

- Dataset (`train`, fixed `anon_id`s, no random sampling — see
  `make_golden.py::HUMAN_ANON_IDS` / `SYNTHETIC_ANON_IDS`): `call_04d682ac0cef`,
  `call_0615c3630150`, `call_092aef8d1243`, `call_0a9c546208d1`,
  `call_0ab7d2a0c0f5` (human); `call_0181ce113ebe`, `call_018c9d3823ac`,
  `call_01bf49059daf`, `call_01c3806808d6`, `call_02c249d2f89d` (synthetic).
- Degenerate: `degenerate_no_caller_turns`, `degenerate_one_turn_each`,
  `degenerate_fully_overlapping`.

## Note on unrelated pre-existing failures

`cargo test --lib` currently fails two unrelated VAD-smoothing tests
(`audio::vad::smooth::tests::merge_close_segments`,
`::three_turns_detected_and_within_tolerance`, an `attempt to subtract with
overflow` panic in `smooth.rs`) on `main` as of this task — out of scope
for T015 (parity concerns `features/` only) and not touched here. `cargo
test --test parity` and `cargo test --lib features::` are both green.
