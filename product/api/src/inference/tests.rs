#[cfg(test)]
mod tests {
    use super::*;
    use crate::inference::model::Model;
    use std::path::PathBuf;
    use std::process::Command;
    use std::fs;

    fn fixtures_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures")
    }

    #[test]
    fn load_dummy_and_score_monotonic() {
        let dir = fixtures_dir();
        let script = dir.join("make_dummy_model.py");
        let status = Command::new("python3")
            .arg(script)
            .status()
            .expect("python3 available");
        assert!(status.success());

        let onnx = dir.join("dummy_fc1.onnx");
        let mut model = Model::load(&onnx).expect("load model");
        let mut last = 0.0;
        for i in 0..5 {
            let mut features = [0.0f64; 23];
            features[0] = i as f64;
            let scored = model.score(features).expect("score");
            assert!(scored.p_synthetic >= last - 1e-12, "monotonic");
            last = scored.p_synthetic;
        }
    }

    #[test]
    fn load_rejects_bad_meta() {
        let dir = fixtures_dir();
        let onnx = dir.join("dummy_fc1.onnx");
        if !onnx.exists() {
            let script = dir.join("make_dummy_model.py");
            let status = Command::new("python3")
                .arg(script)
                .status()
                .expect("python3 available");
            assert!(status.success());
        }
        // copy to a bad pair
        let bad_onnx = dir.join("dummy_bad.onnx");
        let bad_meta = dir.join("dummy_bad.onnx.meta.json");
        fs::copy(&onnx, &bad_onnx).expect("copy onnx");
        let mut meta: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(dir.join("dummy_fc1.onnx.meta.json")).unwrap()).unwrap();
        meta["feature_contract"] = serde_json::json!("fc-2");
        fs::write(&bad_meta, serde_json::to_string(&meta).unwrap()).unwrap();

        let res = Model::load(&bad_onnx);
        assert!(res.is_err(), "expected load to fail on wrong contract");
    }
}

