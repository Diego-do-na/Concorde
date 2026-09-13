use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use tokio::sync::broadcast;

use crate::pipeline::Analysis;

/// Public-facing, small event emitted into the live feed.
#[derive(Debug, Clone, Serialize)]
pub struct FeedSignals {
    pub behavioral: bool,
    pub semantic: Option<bool>,
    pub acoustic: Option<bool>,
}

#[derive(Debug, Clone, Serialize)]
pub struct FeedEvent {
    pub id: String,
    /// epoch seconds with fractional component
    pub ts: f64,
    pub duration_s: f32,
    pub is_synthetic: bool,
    pub confidence: f64,
    pub latency_ms: f64,
    pub signals: FeedSignals,
    pub model_version: Option<String>,
}

/// In-memory ring and recent-analysis store with a broadcast channel for
/// websocket subscribers. Thread-safe wrapper.
pub struct Feed {
    inner: Mutex<FeedInner>,
    tx: broadcast::Sender<FeedEvent>,
    /// active WS connections counter for basic rate-limiting
    active_ws: AtomicUsize,
    max_ws: usize,
}

struct FeedInner {
    ring: VecDeque<FeedEvent>,
    analyses: HashMap<String, Analysis>,
    counter: u64,
    ring_capacity: usize,
    analyses_capacity: usize,
}

impl Feed {
    pub fn new(ring_capacity: usize, analyses_capacity: usize, max_ws: usize) -> Arc<Self> {
        let (tx, _rx) = broadcast::channel(1024);
        Arc::new(Self {
            inner: Mutex::new(FeedInner {
                ring: VecDeque::with_capacity(ring_capacity),
                analyses: HashMap::new(),
                counter: 0,
                ring_capacity,
                analyses_capacity,
            }),
            tx,
            active_ws: AtomicUsize::new(0),
            max_ws,
        })
    }

    /// Push a fresh event derived from an `Analysis`. `call_id` may be used
    /// as the stable id if present; otherwise a monotonic counter id is
    /// generated.
    pub fn push(&self, call_id: Option<String>, analysis: &Analysis) -> String {
        let mut inner = self.inner.lock().unwrap();
        let id = match call_id {
            Some(ref s) if !s.is_empty() => s.clone(),
            _ => {
                inner.counter += 1;
                format!("evt-{}", inner.counter)
            }
        };

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or_default();

        let event = FeedEvent {
            id: id.clone(),
            ts: now,
            duration_s: analysis.duration_s,
            is_synthetic: analysis.verdict.is_synthetic,
            confidence: analysis.verdict.confidence,
            latency_ms: analysis.timings_ms.total,
            signals: FeedSignals {
                behavioral: true,
                semantic: None,
                acoustic: None,
            },
            model_version: analysis.model_version.clone(),
        };

        // push into ring (keep newest at back)
        inner.ring.push_back(event.clone());
        while inner.ring.len() > inner.ring_capacity {
            inner.ring.pop_front();
        }

        // retain full analysis for this id (bounded)
        inner.analyses.insert(id.clone(), analysis.clone());
        while inner.analyses.len() > inner.analyses_capacity {
            // evict oldest by inspecting ring front (best-effort)
            if let Some(old_id) = inner.ring.front().map(|e| e.id.clone()) {
                inner.analyses.remove(&old_id);
            } else {
                break;
            }
        }

        // broadcast to subscribers (ignore send errors when no subscribers)
        let _ = self.tx.send(event);
        id
    }

    /// Return up to `limit` most-recent events, newest-first.
    pub fn recent(&self, limit: usize) -> Vec<FeedEvent> {
        let inner = self.inner.lock().unwrap();
        let mut v: Vec<FeedEvent> = inner.ring.iter().cloned().collect();
        v.reverse();
        v.truncate(limit);
        v
    }

    pub fn get_analysis(&self, id: &str) -> Option<Analysis> {
        let inner = self.inner.lock().unwrap();
        inner.analyses.get(id).cloned()
    }

    pub fn subscribe(&self) -> broadcast::Receiver<FeedEvent> {
        self.tx.subscribe()
    }

    pub fn try_acquire_ws_slot(&self) -> bool {
        let before = self.active_ws.fetch_add(1, Ordering::SeqCst);
        if before >= self.max_ws {
            // undo and deny
            self.active_ws.fetch_sub(1, Ordering::SeqCst);
            false
        } else {
            true
        }
    }

    pub fn release_ws_slot(&self) {
        let before = self.active_ws.fetch_sub(1, Ordering::SeqCst);
        let _ = before;
    }
}


