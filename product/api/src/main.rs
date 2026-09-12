mod config;
mod state;
mod routes;
mod http;
mod audio;
mod features;
mod inference;
mod analysis;
mod semantic;
mod storage;
mod metrics;
mod feed;

use std::{net::SocketAddr, sync::Arc};
use axum::{routing::get, Router};
use tracing::info;

use crate::state::AppState;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // init tracing (JSON)
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let config = config::Config::from_env();
    let state = Arc::new(AppState::new(config));

    let bind = state.config.bind.parse::<SocketAddr>().unwrap_or_else(|_| {
        "127.0.0.1:8080".parse().expect("static default valid")
    });

    let app = Router::new().route("/health", get(routes::health::health)).with_state(state);

    info!(%bind, "starting concorde API");
    axum::Server::bind(&bind)
        .serve(app.into_make_service())
        .await
        .map_err(|e| anyhow::anyhow!(e))?;

    Ok(())
}

