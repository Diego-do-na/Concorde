use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicU64, Ordering};

use hdrhistogram::Histogram;
use once_cell::sync::Lazy;
use serde::Serialize;

/// Status of an external dependency as reported into the registry.
#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum DepStatus {
    Ok,
    Degraded,
    Disabled,
}

impl ToString for DepStatus {
    fn to_string(&self) -> String {
        match self {
            DepStatus::Ok => "ok".to_string(),
            DepStatus::Degraded => "degraded".to_string(),
            DepStatus::Disabled => "disabled".to_string(),
        }
    }
}

pub type InnerHistogram = Histogram<u64>;

#[derive(Clone)]
pub struct Metrics {
    // counters keyed by name (e.g. "requests", "fallbacks", "route:detect:ok")
    counters: Arc<Mutex<HashMap<String, u64>>>,
    // histograms per route (milliseconds recorded as micros u64)
    histograms: Arc<Mutex<HashMap<String, InnerHistogram>>>,
    // global fallback counter
    fallbacks: Arc<AtomicU64>,
}

impl Metrics {
    pub fn new() -> Self {
        Self {
            counters: Arc::new(Mutex::new(HashMap::new())),
            histograms: Arc::new(Mutex::new(HashMap::new())),
            fallbacks: Arc::new(AtomicU64::new(0)),
        }
    }

    /// Record one request: route (e.g. "detect"), outcome (e.g. "ok"|"fallback"),
    /// duration_ms is a f64 in milliseconds, fallback indicates fallback occurred.
    pub fn record(&self, route: &str, outcome: &str, duration_ms: f64, fallback: bool) {
        let mut counters = self.counters.lock().unwrap();
        *counters.entry("requests".to_string()).or_insert(0) += 1;
        *counters.entry(format!("route:{}", route)).or_insert(0) += 1;
        *counters.entry(format!("route:{}:outcome:{}", route, outcome)).or_insert(0) += 1;

        if fallback {
            self.fallbacks.fetch_add(1, Ordering::Relaxed);
            *counters.entry("fallbacks".to_string()).or_insert(0) += 1;
        }

        // histogram key per route
        let key = route.to_string();
        let mut hs = self.histograms.lock().unwrap();
        let hist = hs.entry(key.clone()).or_insert_with(|| {
            // track durations from 1 microsecond to 10s in micros with 3 sig figs
            Histogram::<u64>::new_with_max(10_000_000_u64, 3).expect("histogram create")
        });
        // convert ms to microseconds u64
        let us = (duration_ms * 1000.0).max(0.0).round() as u64;
        let _ = hist.record(us);
    }

    /// Snapshot metrics as JSON-serializable structure.
    pub fn snapshot_json(&self) -> serde_json::Value {
        let counters = self.counters.lock().unwrap();
        let hs = self.histograms.lock().unwrap();

        let mut routes = serde_json::Map::new();
        for (route, hist) in hs.iter() {
            let p50 = hist.value_at_quantile(0.50) as f64 / 1000.0;
            let p95 = hist.value_at_quantile(0.95) as f64 / 1000.0;
            let p99 = hist.value_at_quantile(0.99) as f64 / 1000.0;
            let count = counters.get(&format!("route:{}", route)).copied().unwrap_or(0);
            let mut r = serde_json::Map::new();
            r.insert("count".to_string(), serde_json::Value::from(count));
            r.insert("p50_ms".to_string(), serde_json::Value::from(p50));
            r.insert("p95_ms".to_string(), serde_json::Value::from(p95));
            r.insert("p99_ms".to_string(), serde_json::Value::from(p99));
            routes.insert(route.clone(), serde_json::Value::Object(r));
        }

        let mut top = serde_json::Map::new();
        for (k, v) in counters.iter() {
            top.insert(k.clone(), serde_json::Value::from(*v));
        }
        top.insert("routes".to_string(), serde_json::Value::Object(routes));
        top.insert("fallbacks".to_string(), serde_json::Value::from(self.fallbacks.load(Ordering::Relaxed)));
        serde_json::Value::Object(top)
    }

    /// Render a tiny Prometheus-like exposition (text/plain).
    pub fn render_prometheus(&self) -> String {
        let mut out = String::new();
        let counters = self.counters.lock().unwrap();
        for (k, v) in counters.iter() {
            out.push_str(&format!("concorde_{} {}\n", k.replace(':', "_"), v));
        }
        let hs = self.histograms.lock().unwrap();
        for (route, hist) in hs.iter() {
            let p50 = hist.value_at_quantile(0.50) as f64 / 1000.0;
            let p95 = hist.value_at_quantile(0.95) as f64 / 1000.0;
            let p99 = hist.value_at_quantile(0.99) as f64 / 1000.0;
            out.push_str(&format!("concorde_route_latency_ms{{route=\"{}\",quantile=\"50\"}} {}\n", route, p50));
            out.push_str(&format!("concorde_route_latency_ms{{route=\"{}\",quantile=\"95\"}} {}\n", route, p95));
            out.push_str(&format!("concorde_route_latency_ms{{route=\"{}\",quantile=\"99\"}} {}\n", route, p99));
        }
        out
    }
}

// A global metrics instance used by code that doesn't hold AppState.
pub static GLOBAL_METRICS: Lazy<Metrics> = Lazy::new(|| Metrics::new());


