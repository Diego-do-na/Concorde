use std::{
    net::SocketAddr,
    sync::{Arc, Mutex},
};
use tracing::info;

use concorde_api::{AppState, Config};
use concorde_api::routes;
use concorde_api::inference::model::Model;


#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // init tracing (JSON)
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let config = Config::from_env();

    // load model eagerly if configured (abort on mismatch)
    let model = if let Some(path) = config.model_path.clone() {
        match Model::load(std::path::Path::new(&path)) {
            Ok(m) => Some(Arc::new(Mutex::new(m))),
            Err(e) => {
                tracing::error!(error=%e, "failed to load model, aborting startup");
                return Err(e);
            }
        }
    } else {
        None
    };

    // Boot the semantic layer (T045) eagerly, alongside the ONNX model:
    // one WhisperContext for the process lifetime, never per-request.
    // Never aborts startup -- a disabled/failed semantic layer still
    // serves the MUST-priority behavioral `/detect` path (AGENTS.md
    // rule 3); `SemanticEngine::boot` logs loudly on failure instead.
    let semantic = concorde_api::semantic::SemanticEngine::boot(&config);

    let state = Arc::new(AppState::new_with_semantic(config, model, semantic));

    let bind = state.config.bind.parse::<SocketAddr>().unwrap_or_else(|_| {
        "127.0.0.1:8080".parse().expect("static default valid")
    });

    let app = routes::router().with_state(state);

    info!(%bind, "starting concorde API");
    axum::Server::bind(&bind)
        .serve(app.into_make_service())
        .await
        .map_err(|e| anyhow::anyhow!(e))?;

    Ok(())
}

