use std::env;

#[derive(Clone, Debug)]
pub struct Config {
    pub bind: String,
    pub model_path: Option<String>,
    pub feature_contract: String,
    pub threshold: Option<f32>,
    pub max_body_bytes: usize,
    pub handler_timeout_ms: u64,
    pub strict: bool,
    pub semantic_enabled: bool,
    pub semantic_timeout_ms: u64,
    pub analyze_semantic_timeout_ms: u64,
    pub whisper_model_path: Option<String>,
    pub whisper_threads: Option<usize>,
    pub asr_max_concurrent: Option<usize>,
    pub semantic_fusion_path: Option<String>,
    pub tigerdata_url: Option<String>,
}

impl Config {
    pub fn from_env() -> Self {
        let bind = env::var("CONCORDE_BIND").unwrap_or_else(|_| "127.0.0.1:8080".to_string());
        let model_path = env::var("CONCORDE_MODEL_PATH").ok();
        let feature_contract =
            env::var("CONCORDE_FEATURE_CONTRACT").unwrap_or_else(|_| "fc-1".to_string());
        let threshold = env::var("CONCORDE_THRESHOLD").ok().and_then(|s| s.parse().ok());
        let max_body_bytes = env::var("CONCORDE_MAX_BODY_BYTES")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(16_777_216usize);
        let handler_timeout_ms = env::var("CONCORDE_HANDLER_TIMEOUT_MS")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(20_000u64);
        let strict = env::var("CONCORDE_STRICT")
            .map(|s| matches!(s.as_str(), "1" | "true" | "TRUE" | "yes"))
            .unwrap_or(false);
        let semantic_enabled = env::var("CONCORDE_SEMANTIC_ENABLED")
            .map(|s| matches!(s.as_str(), "1" | "true" | "TRUE" | "yes"))
            .unwrap_or(false);
        let semantic_timeout_ms = env::var("CONCORDE_SEMANTIC_TIMEOUT_MS")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(1500u64);
        let analyze_semantic_timeout_ms = env::var("CONCORDE_ANALYZE_SEMANTIC_TIMEOUT_MS")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(8000u64);
        let whisper_model_path = env::var("CONCORDE_WHISPER_MODEL_PATH").ok();
        let whisper_threads = env::var("CONCORDE_WHISPER_THREADS").ok().and_then(|s| s.parse().ok());
        let asr_max_concurrent =
            env::var("CONCORDE_ASR_MAX_CONCURRENT").ok().and_then(|s| s.parse().ok());
        let semantic_fusion_path = env::var("CONCORDE_SEMANTIC_FUSION_PATH").ok();
        let tigerdata_url = env::var("TIGERDATA_URL").ok();

        Self {
            bind,
            model_path,
            feature_contract,
            threshold,
            max_body_bytes,
            handler_timeout_ms,
            strict,
            semantic_enabled,
            semantic_timeout_ms,
            analyze_semantic_timeout_ms,
            whisper_model_path,
            whisper_threads,
            asr_max_concurrent,
            semantic_fusion_path,
            tigerdata_url,
        }
    }
}

