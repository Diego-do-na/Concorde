use std::{net::SocketAddr, sync::Arc};
use tracing::info;

use concorde_api::{AppState, Config};
use concorde_api::routes;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // init tracing (JSON)
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    let config = Config::from_env();
    let state = Arc::new(AppState::new(config));

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

