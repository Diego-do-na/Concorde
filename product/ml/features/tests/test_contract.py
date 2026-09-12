from features.contract import CONTRACT_ID, assert_contract_matches_extractor, load_contract
from features.extract import FEATURE_NAMES


def test_contract_has_23_features():
    _contract_id, names = load_contract()
    assert len(names) == 23


def test_contract_id_is_fc1():
    contract_id, _names = load_contract()
    assert contract_id == "fc-1" == CONTRACT_ID


def test_contract_matches_extractor_names_and_order():
    assert_contract_matches_extractor()
    _contract_id, names = load_contract()
    assert names == FEATURE_NAMES
    assert len(FEATURE_NAMES) == 23


def test_f21_split_into_two_scalar_features():
    # F-21 (silence_break_delay) is defined in §9 as "mean and CV (2
    # values)", which is exactly why fc-1 has 23 floats for 22 feature IDs.
    _contract_id, names = load_contract()
    assert "silence_break_delay_mean" in names
    assert "silence_break_delay_cv" in names
