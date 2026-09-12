import pathlib


def test_report_exists_and_passes_gate():
    report = pathlib.Path(__file__).resolve().parent / "REPORT.md"
    assert report.exists(), "REPORT.md not found; run sweep.py and run_agreement.py to generate it"
    text = report.read_text(encoding="utf-8")
    assert "FR-004 VAD agreement gate: PASS" in text, "VAD agreement gate did not PASS"

