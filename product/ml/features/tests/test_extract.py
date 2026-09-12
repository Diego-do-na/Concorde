"""Hand-computed vectors for the fc-1 reference extractor (§9).

Every dialogue's expected numbers are worked out by hand in the comments
next to each test — none of them are derived by calling `extract()` on
itself, since that would just test that the code agrees with itself.
"""

from __future__ import annotations

import numpy as np
import pytest

from features.extract import FEATURE_NAMES, extract

IDX = {name: i for i, name in enumerate(FEATURE_NAMES)}


def _feat(vec: np.ndarray, name: str) -> float:
    return float(vec[IDX[name]])


def test_no_nan_or_inf_smoke():
    caller = [(2.5, 4.0), (6.3, 7.0)]
    agent = [(0.0, 2.0), (4.0, 6.0)]
    vec = extract(caller, agent, 8.0)
    assert vec.shape == (23,)
    assert np.isfinite(vec).all()


def test_dialogue_1_no_overlap_sequential_turns():
    # A1(0,2) C1(2.5,4) A2(4,6) C2(6.3,7) A3(7,9) C3(9.4,11); D = 11.0
    agent = [(0.0, 2.0), (4.0, 6.0), (7.0, 9.0)]
    caller = [(2.5, 4.0), (6.3, 7.0), (9.4, 11.0)]
    duration_s = 11.0
    vec = extract(caller, agent, duration_s)

    # latencies = [2.5-2.0, 6.3-6.0, 9.4-9.0] = [0.5, 0.3, 0.4]
    # mean=0.4, median=0.4, popstd=sqrt(((0.1)^2+(0.1)^2+0)/3)=0.081649658...
    # cv = std/mean = 0.204124145...
    assert _feat(vec, "resp_latency_mean") == pytest.approx(0.4, abs=1e-9)
    assert _feat(vec, "resp_latency_median") == pytest.approx(0.4, abs=1e-9)
    assert _feat(vec, "resp_latency_std") == pytest.approx(0.0816496581, abs=1e-9)
    assert _feat(vec, "resp_latency_cv") == pytest.approx(0.2041241452, abs=1e-9)
    # all three latencies fall within +/-0.15 of the median 0.4 -> 3/3
    assert _feat(vec, "latency_monotony_index") == pytest.approx(1.0, abs=1e-9)
    assert _feat(vec, "resp_latency_min") == pytest.approx(0.3, abs=1e-9)

    # no (agent, caller) pair intersects anywhere in this dialogue
    assert _feat(vec, "overlap_count") == 0.0
    assert _feat(vec, "overlap_rate_per_min") == 0.0
    assert _feat(vec, "overlap_total_dur") == 0.0
    assert _feat(vec, "caller_bargein_count") == 0.0
    assert _feat(vec, "recovery_delay_mean") == 0.0
    assert _feat(vec, "recovery_delay_cv") == 0.0
    assert _feat(vec, "recovery_abort_ratio") == 0.0

    # caller durations = [1.5, 0.7, 1.6]; mean=1.266667, popstd=0.402768, cv=0.317975
    assert _feat(vec, "caller_turn_dur_mean") == pytest.approx(1.2666666667, abs=1e-9)
    assert _feat(vec, "caller_turn_dur_std") == pytest.approx(0.4027681991, abs=1e-9)
    assert _feat(vec, "caller_turn_dur_cv") == pytest.approx(0.3179748940, abs=1e-9)
    # only C2 (0.7s) is < 1.0s -> 1/3
    assert _feat(vec, "short_turn_ratio") == pytest.approx(1.0 / 3.0, abs=1e-9)
    # gaps between consecutive caller turns: 2.3 and 2.4, neither < 0.5s
    assert _feat(vec, "fragmentation_rate") == 0.0

    # caller total = 3.8s / D=11.0 = 0.345455; agent total = 6.0
    assert _feat(vec, "caller_speech_ratio") == pytest.approx(0.3454545455, abs=1e-9)
    assert _feat(vec, "speech_balance") == pytest.approx(0.6333333333, abs=1e-9)

    # no mutual-silence gap ever exceeds 2.0s in this dialogue
    assert _feat(vec, "silence_break_delay_mean") == 0.0
    assert _feat(vec, "silence_break_delay_cv") == 0.0

    assert _feat(vec, "turn_count_caller") == 3.0
    assert np.isfinite(vec).all()


def test_dialogue_2_bargein_abort_fragmentation_and_silence():
    # A1(0,5) with a caller barge-in+abort C1(2.0,2.3) inside it,
    # then C2(5.2,5.4) short backchannel, a 3.1s mutual silence,
    # A2(8.5,10.0), C3(10.6,12.0), then fragmented C4(12.3,13.0). D=13.0
    agent = [(0.0, 5.0), (8.5, 10.0)]
    caller = [(2.0, 2.3), (5.2, 5.4), (10.6, 12.0), (12.3, 13.0)]
    duration_s = 13.0
    vec = extract(caller, agent, duration_s)

    # A1 ends at 5.0 -> first caller turn with start>=5.0 is C2(5.2): latency=0.2
    # A2 ends at 10.0 -> first caller turn with start>=10.0 is C3(10.6): latency=0.6
    # latencies=[0.2,0.6]; mean=0.4, median=0.4, popstd=0.2, cv=0.5
    assert _feat(vec, "resp_latency_mean") == pytest.approx(0.4, abs=1e-9)
    assert _feat(vec, "resp_latency_std") == pytest.approx(0.2, abs=1e-9)
    assert _feat(vec, "resp_latency_cv") == pytest.approx(0.5, abs=1e-9)
    # both latencies are 0.2 away from the median 0.4 -> neither within +/-0.15
    assert _feat(vec, "latency_monotony_index") == 0.0
    assert _feat(vec, "resp_latency_min") == pytest.approx(0.2, abs=1e-9)

    # only (A1, C1) intersects: overlap_start=max(0,2.0)=2.0, overlap_end=min(5.0,2.3)=2.3
    assert _feat(vec, "overlap_count") == 1.0
    assert _feat(vec, "overlap_rate_per_min") == pytest.approx(60.0 / 13.0, abs=1e-9)
    assert _feat(vec, "overlap_total_dur") == pytest.approx(0.3, abs=1e-9)
    # C1 starts strictly inside A1(0,5) -> 1 barge-in
    assert _feat(vec, "caller_bargein_count") == 1.0

    # recovery: after overlap ends at 2.3, first caller turn with start>=2.3 is C2(5.2)
    # -> recovery_delay = 5.2-2.3 = 2.9 (single value -> popstd=0 -> cv=0)
    assert _feat(vec, "recovery_delay_mean") == pytest.approx(2.9, abs=1e-9)
    assert _feat(vec, "recovery_delay_cv") == 0.0
    # abort: C1 ends at 2.3, overlap onset=max(0,2.0)=2.0 -> 2.3-2.0=0.3 <= 0.4 -> abort
    assert _feat(vec, "recovery_abort_ratio") == pytest.approx(1.0, abs=1e-9)

    # caller durations = [0.3, 0.2, 1.4, 0.7]; mean=0.65, popstd=0.471699, cv=0.725691
    assert _feat(vec, "caller_turn_dur_mean") == pytest.approx(0.65, abs=1e-9)
    assert _feat(vec, "caller_turn_dur_std") == pytest.approx(0.4716990566, abs=1e-9)
    assert _feat(vec, "caller_turn_dur_cv") == pytest.approx(0.7256908563, abs=1e-9)
    # C1(0.3), C2(0.2), C4(0.7) are < 1.0s -> 3/4
    assert _feat(vec, "short_turn_ratio") == pytest.approx(0.75, abs=1e-9)
    # only the C3->C4 gap (12.3-12.0=0.3s) is < 0.5s -> 1 fragmentation event
    assert _feat(vec, "fragmentation_rate") == pytest.approx(60.0 / 13.0, abs=1e-9)

    # caller total = 2.6s / D=13.0 = 0.2; agent total = 5.0+1.5 = 6.5
    assert _feat(vec, "caller_speech_ratio") == pytest.approx(0.2, abs=1e-9)
    assert _feat(vec, "speech_balance") == pytest.approx(2.6 / 6.5, abs=1e-9)

    # the only >2.0s mutual silence is 5.4 -> 8.5 (3.1s); first caller turn at
    # or after 5.4 is C3(10.6) -> delay = 10.6-5.4 = 5.2 (single value -> cv=0)
    assert _feat(vec, "silence_break_delay_mean") == pytest.approx(5.2, abs=1e-9)
    assert _feat(vec, "silence_break_delay_cv") == 0.0

    assert _feat(vec, "turn_count_caller") == 4.0
    assert np.isfinite(vec).all()


def test_dialogue_3_overlap_wins_the_floor_no_next_turn():
    # A1(0.0,1.0); caller barges in at 0.2 and keeps talking well past the
    # agent's turn end: C1(0.2,3.0). C1 is also the last (and only) caller
    # turn, so there is no "next caller turn" to recover into.
    agent = [(0.0, 1.0)]
    caller = [(0.2, 3.0)]
    duration_s = 3.3
    vec = extract(caller, agent, duration_s)

    # A1 ends at 1.0; the only caller turn starts at 0.2 < 1.0, so no caller
    # turn qualifies as "first turn with start >= a.end" -> empty latency set
    assert _feat(vec, "resp_latency_mean") == 0.0
    assert _feat(vec, "resp_latency_std") == 0.0
    assert _feat(vec, "resp_latency_cv") == 0.0
    assert _feat(vec, "latency_monotony_index") == 0.0
    assert _feat(vec, "resp_latency_min") == 0.0

    # (A1, C1) intersects: overlap_start=max(0,0.2)=0.2, overlap_end=min(1.0,3.0)=1.0
    assert _feat(vec, "overlap_count") == 1.0
    assert _feat(vec, "overlap_rate_per_min") == pytest.approx(60.0 / 3.3, abs=1e-9)
    assert _feat(vec, "overlap_total_dur") == pytest.approx(0.8, abs=1e-9)
    assert _feat(vec, "caller_bargein_count") == 1.0

    # recovery: overlap ends at 1.0; no caller turn starts at/after 1.0 (C1
    # itself starts at 0.2) -> recovery_delays is empty -> degenerate 0.0
    assert _feat(vec, "recovery_delay_mean") == 0.0
    assert _feat(vec, "recovery_delay_cv") == 0.0
    # abort check uses C1's own end (3.0) vs overlap onset (0.2): 2.8 > 0.4 -> not an abort
    assert _feat(vec, "recovery_abort_ratio") == 0.0

    # a single caller turn of duration 2.8s -> std/cv are 0 for a singleton
    assert _feat(vec, "caller_turn_dur_mean") == pytest.approx(2.8, abs=1e-9)
    assert _feat(vec, "caller_turn_dur_std") == 0.0
    assert _feat(vec, "caller_turn_dur_cv") == 0.0
    assert _feat(vec, "short_turn_ratio") == 0.0
    # a single caller turn has no consecutive pair to fragment
    assert _feat(vec, "fragmentation_rate") == 0.0

    assert _feat(vec, "caller_speech_ratio") == pytest.approx(2.8 / 3.3, abs=1e-9)
    assert _feat(vec, "speech_balance") == pytest.approx(2.8, abs=1e-9)

    assert _feat(vec, "silence_break_delay_mean") == 0.0
    assert _feat(vec, "silence_break_delay_cv") == 0.0

    assert _feat(vec, "turn_count_caller") == 1.0
    assert np.isfinite(vec).all()


def test_spec_sample_fragmentation_visible_at_126_32_129_14():
    # §9's own worked example: a caller self-repair visible at
    # 126.32-129.14 / 129.46-131.26 (gap = 129.46-129.14 = 0.32s < 0.5s).
    caller = [(126.32, 129.14), (129.46, 131.26)]
    agent: list = []
    duration_s = 140.0
    vec = extract(caller, agent, duration_s)

    assert _feat(vec, "fragmentation_rate") == pytest.approx(60.0 / 140.0, abs=1e-9)
    assert np.isfinite(vec).all()


def test_degenerate_no_caller_turns():
    agent = [(0.0, 2.0), (3.0, 5.0)]
    caller: list = []
    duration_s = 6.0
    vec = extract(caller, agent, duration_s)

    assert np.isfinite(vec).all()
    assert not np.isnan(vec).any()
    # every latency/overlap/recovery/morphology quantity keyed off caller
    # turns collapses to the documented degenerate default of 0.0
    for name in [
        "resp_latency_mean", "resp_latency_median", "resp_latency_std",
        "resp_latency_cv", "latency_monotony_index", "resp_latency_min",
        "overlap_count", "overlap_rate_per_min", "overlap_total_dur",
        "caller_bargein_count", "recovery_delay_mean", "recovery_delay_cv",
        "recovery_abort_ratio", "caller_turn_dur_mean", "caller_turn_dur_std",
        "caller_turn_dur_cv", "short_turn_ratio", "fragmentation_rate",
        "caller_speech_ratio", "speech_balance", "silence_break_delay_mean",
        "silence_break_delay_cv",
    ]:
        assert _feat(vec, name) == 0.0, name
    assert _feat(vec, "turn_count_caller") == 0.0


def test_degenerate_single_caller_turn_no_agent_turns():
    agent: list = []
    caller = [(1.0, 2.0)]
    duration_s = 3.0
    vec = extract(caller, agent, duration_s)

    assert np.isfinite(vec).all()
    # no agent turns -> no response-latency observations at all
    assert _feat(vec, "resp_latency_mean") == 0.0
    assert _feat(vec, "resp_latency_min") == 0.0
    # no agent turns -> no overlaps possible
    assert _feat(vec, "overlap_count") == 0.0
    assert _feat(vec, "caller_bargein_count") == 0.0
    # a single 1.0s turn: mean=1.0, std/cv=0 (singleton)
    assert _feat(vec, "caller_turn_dur_mean") == pytest.approx(1.0, abs=1e-9)
    assert _feat(vec, "caller_turn_dur_std") == 0.0
    assert _feat(vec, "caller_turn_dur_cv") == 0.0
    # duration is exactly 1.0s, not strictly < 1.0s -> boundary excluded
    assert _feat(vec, "short_turn_ratio") == 0.0
    # a single caller turn has no consecutive pair
    assert _feat(vec, "fragmentation_rate") == 0.0
    assert _feat(vec, "caller_speech_ratio") == pytest.approx(1.0 / 3.0, abs=1e-9)
    # agent total is 0 -> ratio degenerates to 0.0, even though caller spoke
    assert _feat(vec, "speech_balance") == 0.0
    assert _feat(vec, "turn_count_caller") == 1.0


def test_fully_empty_call():
    vec = extract([], [], 10.0)
    assert vec.shape == (23,)
    assert np.isfinite(vec).all()
    assert not np.isnan(vec).any()
    assert not np.isinf(vec).any()
    assert vec.sum() == 0.0


def test_zero_duration_never_divides_by_zero():
    caller = [(0.0, 0.5)]
    agent = [(0.5, 1.0)]
    vec = extract(caller, agent, 0.0)
    assert np.isfinite(vec).all()
    assert _feat(vec, "overlap_rate_per_min") == 0.0
    assert _feat(vec, "fragmentation_rate") == 0.0
    assert _feat(vec, "caller_speech_ratio") == 0.0


def test_unsorted_input_is_accepted():
    agent = [(4.0, 6.0), (0.0, 2.0), (7.0, 9.0)]
    caller = [(9.4, 11.0), (2.5, 4.0), (6.3, 7.0)]
    vec_unsorted = extract(caller, agent, 11.0)
    vec_sorted = extract(sorted(caller), sorted(agent), 11.0)
    assert np.allclose(vec_unsorted, vec_sorted)
