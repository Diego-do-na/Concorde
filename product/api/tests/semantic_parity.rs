//! Semantic layer parity test (T045): Rust rules / probe detector vs Python rules / probe detector.

use concorde_api::semantic::rules::{analyze_answer, classify_answer_type, AnswerType};
use std::process::Command;

const FIXTURES: &[(&str, &str, f64)] = &[
    ("no tengo esa cuenta", "denial", 0.05),
    ("no cuento con eso", "denial", 0.05),
    ("no sé, no me acuerdo", "denial", 0.05),
    ("no me aparece ninguna", "denial", 0.05),
    ("creo que sí tengo algo", "hedge", 0.35),
    ("no estoy seguro, tal vez", "hedge", 0.35),
    ("sí, tengo la nómina plus", "assertion_product", 0.85),
    ("mi crédito verde está activo", "assertion_product", 0.85),
    ("el número es 4 5 6 7 8 9", "assertion_numeric", 0.90),
    ("me llamo Roberto Sánchez", "assertion_name", 0.80),
    ("¿cuál es? no entiendo", "question_back", 0.15),
    ("buenos días como esta", "other", 0.50),
];

#[test]
fn rust_rules_match_python_fixtures_100_percent() {
    for &(phrase, expected_type, expected_score) in FIXTURES {
        let (atype, score) = analyze_answer(phrase);
        assert_eq!(
            atype.as_str(),
            expected_type,
            "phrase {:?} expected type {}, got {}",
            phrase,
            expected_type,
            atype.as_str()
        );
        assert!(
            (score - expected_score).abs() < 1e-6,
            "phrase {:?} expected score {}, got {}",
            phrase,
            expected_score,
            score
        );
    }
}

#[test]
fn semantic_dump_binary_runs_and_emits_valid_json() {
    let spec = hound::WavSpec {
        channels: 2,
        sample_rate: 8000,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let dir = std::env::temp_dir();
    let wav_path = dir.join("test_semantic_dump_input.wav");
    let mut writer = hound::WavWriter::create(&wav_path, spec).unwrap();
    for i in 0..8000i16 {
        writer.write_sample(i).unwrap();
        writer.write_sample(-i).unwrap();
    }
    writer.finalize().unwrap();

    let exe = env!("CARGO_BIN_EXE_semantic-dump");
    let output = Command::new(exe)
        .arg(&wav_path)
        .output()
        .expect("exec semantic-dump");

    assert!(output.status.success(), "semantic-dump failed: stderr: {}", String::from_utf8_lossy(&output.stderr));

    let stdout = String::from_utf8(output.stdout).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(&stdout)
        .unwrap_or_else(|e| panic!("invalid json from semantic-dump: stdout={:?}, err={}", stdout, e));

    assert!(parsed.get("probe_detected").is_some());
    assert!(parsed.get("probe_t").is_some());
    assert!(parsed.get("answer_type").is_some());
    assert!(parsed.get("invention_score").is_some());

    let _ = std::fs::remove_file(&wav_path);
}
