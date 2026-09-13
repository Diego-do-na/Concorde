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
    /// Insertion order of `analyses` keys (oldest first, may hold stale
    /// duplicates for ids pushed more than once). Eviction uses THIS, not
    /// the event ring: a call_id re-sent later (val replays, demos) has an
    /// old ring event, and evicting by "oldest ring event still in the map"
    /// deleted the freshly retained analysis instead of the oldest one.
    analysis_order: VecDeque<String>,
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
                analysis_order: VecDeque::new(),
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
        inner.analysis_order.push_back(id.clone());
        // Evict the analyses that were retained longest ago. Every iteration
        // pops exactly one queue entry, so the loop always terminates (the
        // first version of this loop spun forever on the 51st call; the
        // second evicted re-sent ids by their oldest ring event).
        {
            let FeedInner { analyses, analysis_order, analyses_capacity, .. } = &mut *inner;
            while analyses.len() > *analyses_capacity {
                match analysis_order.pop_front() {
                    Some(old) => {
                        // stale duplicate: a newer push of the same id is still queued
                        if analysis_order.iter().any(|x| *x == old) {
                            continue;
                        }
                        analyses.remove(&old);
                    }
                    None => {
                        // queue exhausted (should not happen): drop an arbitrary key
                        match analyses.keys().next().cloned() {
                            Some(k) => {
                                analyses.remove(&k);
                            }
                            None => break,
                        }
                    }
                }
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



#[cfg(test)]
mod tests {
    use super::Feed;
    use crate::pipeline::{Analysis, TimingsMs, TurnCounts, Verdict};

    fn dummy_analysis() -> Analysis {
        Analysis {
            duration_s: 1.0,
            turns: vec![],
            turn_counts: TurnCounts { caller: 1, agent: 1 },
            events: vec![],
            features: vec![],
            verdict: Verdict { p_synthetic: 0.5, is_synthetic: false, confidence: 0.75 },
            timings_ms: TimingsMs { decode: 1.0, vad: 1.0, features: 1.0, inference: 1.0, total: 4.0 },
            model_version: Some("dummy".to_string()),
            waveform: crate::analysis::waveform::Waveform { caller: vec![], agent: vec![], bucket_ms: 50 },
            semantic: crate::semantic::SemanticOutcome::disabled(0.5),
        }
    }

    /// Regression: 60 distinct call_ids with analyses_capacity 50 used to
    /// spin forever on the 51st push (see the comment in `push`).
    #[test]
    fn sixty_distinct_pushes_with_capacity_fifty_terminate_and_evict_oldest() {
        let feed = Feed::new(500, 50, 32);
        let a = dummy_analysis();
        for i in 0..60 {
            feed.push(Some(format!("call_{i:03}")), &a);
        }
        assert_eq!(feed.recent(1000).len(), 60);
        assert!(feed.get_analysis("call_000").is_none(), "oldest analysis must be evicted");
        assert!(feed.get_analysis("call_009").is_none());
        assert!(feed.get_analysis("call_010").is_some(), "newest 50 must be retained");
        assert!(feed.get_analysis("call_059").is_some());
    }

    /// A call analysed twice (POST /detect then POST /analyze with the same
    /// call_id) must stay retained: the second push overwrites the map entry
    /// and adds a second ring event, and neither may evict it.
    #[test]
    fn double_push_of_the_newest_id_stays_retained() {
        let feed = Feed::new(500, 50, 32);
        let a = dummy_analysis();
        for i in 0..60 {
            feed.push(Some(format!("call_{i:03}")), &a);
        }
        feed.push(Some("call_new".to_string()), &a);
        feed.push(Some("call_new".to_string()), &a);
        assert!(feed.get_analysis("call_new").is_some(), "double-pushed newest id was evicted");
        assert!(feed.get_analysis("call_059").is_some());
        let feed2 = Feed::new(500, 50, 32);
        for i in 0..60 {
            feed2.push(Some(format!("c{i}")), &a);
            feed2.push(Some(format!("c{i}")), &a);
        }
        assert!(feed2.get_analysis("c59").is_some(), "double push pattern evicts the newest");
        // The production pattern: a val call_id first seen long ago is re-sent.
        let feed3 = Feed::new(500, 50, 32);
        for i in 0..120 {
            feed3.push(Some(format!("v{i}")), &a);
        }
        assert!(feed3.get_analysis("v0").is_none());
        feed3.push(Some("v0".to_string()), &a); // re-send an old id
        feed3.push(Some("v0".to_string()), &a); // /detect then /analyze
        assert!(feed3.get_analysis("v0").is_some(), "re-sent old id must be retained (it is the newest analysis)");
        assert!(feed3.get_analysis("v119").is_some());
        assert!(feed3.get_analysis("v70").is_none(), "the oldest retained analysis (v70) must be the one evicted");
    }

    /// Ring rollover: once the ring itself drops old events, eviction must
    /// still make progress (fallback to an arbitrary key).
    #[test]
    fn ring_rollover_still_terminates() {
        let feed = Feed::new(10, 5, 32);
        let a = dummy_analysis();
        for i in 0..100 {
            feed.push(Some(format!("c{i}")), &a);
        }
        assert_eq!(feed.recent(1000).len(), 10);
        assert!(feed.get_analysis("c99").is_some());
    }
}
