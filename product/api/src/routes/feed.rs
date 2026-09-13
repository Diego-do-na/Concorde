use axum::extract::{Path, Query, State, WebSocketUpgrade};
use axum::response::IntoResponse;
use axum::Json;
use axum::http::StatusCode;
use futures_util::stream::StreamExt;
use futures_util::sink::SinkExt;
use serde::Deserialize;
use serde_json::json;
use tokio::sync::broadcast::error::RecvError;

use crate::state::SharedState;

#[derive(Deserialize)]
pub struct RecentQuery {
    pub limit: Option<usize>,
}

pub async fn recent(
    State(state): State<SharedState>,
    Query(q): Query<RecentQuery>,
) -> impl IntoResponse {
    let limit = q.limit.unwrap_or(50).min(500);
    let events = state.feed.recent(limit);
    Json(events)
}

pub async fn analysis_by_id(
    State(state): State<SharedState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    match state.feed.get_analysis(&id) {
        Some(a) => Json(a).into_response(),
        None => (StatusCode::NOT_FOUND, Json(json!({"error":"analysis not retained"}))).into_response(),
    }
}

pub async fn ws(
    State(state): State<SharedState>,
    ws: WebSocketUpgrade,
) -> impl IntoResponse {
    ws.on_upgrade(|socket| async move {
        // basic rate limit
        if !state.feed.try_acquire_ws_slot() {
            drop(socket);
            return;
        }

        let mut rx = state.feed.subscribe();
        let (mut sender, mut _receiver) = socket.split();

        // spawn a task that forwards broadcast events to the websocket
        let send_task = tokio::spawn(async move {
            loop {
                match rx.recv().await {
                    Ok(evt) => {
                        if let Ok(text) = serde_json::to_string(&evt) {
                            if sender.send(axum::extract::ws::Message::Text(text)).await.is_err() {
                                break;
                            }
                        }
                    }
                    Err(RecvError::Lagged(_)) => {
                        // continue; don't terminate on lag
                        continue;
                    }
                    Err(RecvError::Closed) => break,
                }
            }
        });

        // wait for send_task to finish (either ws closed or broadcast closed)
        let _ = send_task.await;
        state.feed.release_ws_slot();
    })
}

