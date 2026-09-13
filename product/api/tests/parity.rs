//! Golden-vector parity test, Python reference extractor <-> Rust
//! production extractor (FR-005, T015).
//!
//! Fixtures live at `product/ml/validation/parity/golden/*.json`, generated
//! by `product/ml/validation/parity/make_golden.py` from the Python
//! reference extractor (`product/ml/features/extract.py`) — that file (not
//! this test) is the single source of truth for what the "expected" vector
//! is. This test only checks that the Rust extractor reproduces it. Any
//! mismatch is fixed by aligning the Rust side to Python unless Python
//! itself violates §9, in which case stop and get Diego's sign-off
//! (AGENTS.md).

use concorde_api::features::{extract, FC1_NAMES};
use serde::Deserialize;
use std::fs;
use std::path::{Path, PathBuf};

// 1e-6, matching api/src/features/mod.rs's own tolerance rationale: the
// production extractor takes f32 turn boundaries, so f32->f64 promotion
// alone introduces ~1e-8 error before any arithmetic runs.
const EPS: f64 = 1e-6;

#[derive(Deserialize)]
struct GoldenFixture {
    caller: Vec<(f64, f64)>,
    agent: Vec<(f64, f64)>,
    duration_s: f64,
    fc1: Vec<f64>,
    contract: String,
}

fn golden_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../ml/validation/parity/golden")
}

fn load_fixture(path: &Path) -> GoldenFixture {
    let data = fs::read_to_string(path)
        .unwrap_or_else(|e| panic!("read golden fixture {}: {e}", path.display()));
    serde_json::from_str(&data)
        .unwrap_or_else(|e| panic!("parse golden fixture {}: {e}", path.display()))
}

#[test]
fn rust_extractor_matches_python_golden_vectors() {
    let dir = golden_dir();
    let mut entries: Vec<PathBuf> = fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("read golden dir {}: {e}", dir.display()))
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().map(|ext| ext == "json").unwrap_or(false))
        .collect();
    entries.sort();
    assert!(
        !entries.is_empty(),
        "no golden fixtures found in {} — run product/ml/validation/parity/make_golden.py",
        dir.display()
    );

    let mut checked = 0usize;
    for path in &entries {
        let fixture = load_fixture(path);
        assert_eq!(
            fixture.contract, "fc-1",
            "{}: golden fixture declares contract {:?}, expected \"fc-1\"",
            path.display(),
            fixture.contract
        );
        assert_eq!(
            fixture.fc1.len(),
            FC1_NAMES.len(),
            "{}: golden fc1 has {} values, extractor contract has {}",
            path.display(),
            fixture.fc1.len(),
            FC1_NAMES.len()
        );

        let caller: Vec<(f32, f32)> = fixture
            .caller
            .iter()
            .map(|&(s, e)| (s as f32, e as f32))
            .collect();
        let agent: Vec<(f32, f32)> = fixture
            .agent
            .iter()
            .map(|&(s, e)| (s as f32, e as f32))
            .collect();

        let actual = extract(&caller, &agent, fixture.duration_s as f32);

        for (i, name) in FC1_NAMES.iter().enumerate() {
            let expected = fixture.fc1[i];
            let got = actual[i];
            assert!(
                (got - expected).abs() <= EPS,
                "{}: first mismatching feature {:?} (index {i}): expected {expected}, got {got} (diff {})",
                path.display(),
                name,
                (got - expected).abs()
            );
        }
        checked += 1;
    }

    // Guards against a golden-dir typo silently checking zero fixtures.
    assert!(
        checked >= 13,
        "expected at least 13 golden fixtures (10 dataset + 3 degenerate), checked {checked}"
    );
}
