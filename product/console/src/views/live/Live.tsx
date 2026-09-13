import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FeedLike, Analysis, SHIPPED_THRESHOLD } from "../../lib/api";
import { useShell } from "../../app/shellContext";
import { mmss, verdictOf, toneFor } from "../../theme/tokens";
import "./live.css";

type Props = {
  /** Injectable for tests; otherwise the shell's single shared feed. */
  feed?: FeedLike;
};

/**
 * A call counts as "in the review band" when its confidence sits close
 * enough to the decision threshold that the verdict is not worth acting on
 * unassisted. Same rule as `verdictOf`'s REVIEW state, stated once.
 */
const REVIEW_BAND = 0.2;

function isInReviewBand(a: Analysis) {
  return Math.abs(a.verdict.confidence - (a.verdict.threshold || SHIPPED_THRESHOLD)) <= REVIEW_BAND;
}

/** A signal that is absent from the event, per §7.2, is not a zero. */
function missingSignals(a: Analysis) {
  return [a.signals.semantic == null, a.signals.acoustic == null].filter(Boolean).length;
}

function fmtPct(n: number) {
  return `${(n * 100).toFixed(1)}%`;
}

function fmtCount(n: number) {
  return n.toLocaleString("en-US");
}

function fmtClock(ts?: number) {
  if (ts == null) return "—";
  // FeedEvent.ts is epoch *seconds* with a fractional part.
  return new Date(ts * 1000).toLocaleTimeString("en-US", { hour12: false });
}

function abbreviate(id: string) {
  return id.length > 16 ? `${id.slice(0, 10)}…${id.slice(-4)}` : id;
}

/** Said as a sentence, because "The feed is ws." is not one. */
const TRANSPORT_SENTENCE: Record<string, string> = {
  WS: "The service is connected and streaming over a websocket.",
  POLLING: "The service is reachable; the websocket is down, so the feed is being polled.",
  OFFLINE: "The feed is not reachable — check that concorde-api is running.",
};

type StatProps = {
  label: string;
  value: string;
  sub: string;
  tone?: "synthetic" | "review" | "verified";
};

const NO_VALUE = "—";

function Stat({ label, value, sub, tone }: StatProps) {
  // The tint is only applied to an actual figure. An em dash rendered in the
  // synthetic red reads as a value the service reported, when in fact it
  // means the service has not reported anything yet.
  const hasValue = value !== NO_VALUE;
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value${tone && hasValue ? ` is-${tone}` : ""}`}>{value}</div>
      <div className="stat-sub">{sub}</div>
    </div>
  );
}

export default function Live({ feed }: Props) {
  const navigate = useNavigate();
  const shell = useShell();
  const feedInstance = feed ?? shell.feed;
  const [items, setItems] = useState<Analysis[]>([]);

  useEffect(() => {
    const unsub = feedInstance.subscribe((incoming) => {
      // Replace wholesale, but only when the identity of the list actually
      // changed — the feed re-emits the same rows on every poll tick.
      setItems((prev) => {
        if (prev.length === incoming.length && prev.every((p, i) => p.id === incoming[i].id)) return prev;
        return incoming;
      });
    });
    // The feed is owned by the shell (or by the caller in tests); this view
    // must not close it, or navigating away would kill it for everyone.
    return unsub;
  }, [feedInstance]);

  const stats = useMemo(() => {
    const n = items.length;
    const synthetic = items.filter((a) => a.verdict.is_synthetic).length;
    const review = items.filter(isInReviewBand).length;
    const degraded = items.filter((a) => missingSignals(a) > 0).length;
    return { n, synthetic, review, degraded };
  }, [items]);

  const p95 = shell.health?.p95_ms;
  const processed = shell.health?.processed;
  const fallbacks = shell.health?.fallbacks;

  // `routes.detect.count` counts every answered request, including the ones
  // that returned the fallback verdict because the input could not be read.
  // Reporting the total alone would show "PROCESSED 4" over an empty feed
  // with no explanation of the gap.
  const processedSub =
    processed != null && fallbacks
      ? `${fallbacks.toLocaleString("en-US")} of these returned the fallback verdict`
      : "/metrics detect counter, since service boot";

  return (
    <div className="monitor">
      {/*
       * Sub-labels name the source of every figure rather than dressing it
       * up. Three of these five are computed over the feed ring — which is
       * the last N calls, not a 24-hour window — and saying so is the
       * difference between a metric and a claim.
       */}
      <div className="stat-strip">
        <Stat
          label="Processed"
          value={processed != null ? fmtCount(processed) : NO_VALUE}
          sub={processedSub}
          tone={processed != null && fallbacks ? "review" : undefined}
        />
        <Stat
          label="Synthetic rate"
          value={stats.n ? fmtPct(stats.synthetic / stats.n) : NO_VALUE}
          sub={stats.n ? `${stats.synthetic} of ${stats.n} calls in feed` : "no calls in feed"}
          tone="synthetic"
        />
        <Stat
          label="Review queue"
          value={stats.n ? fmtCount(stats.review) : NO_VALUE}
          sub={`confidence within ±${REVIEW_BAND.toFixed(2)} of threshold`}
          tone="review"
        />
        <Stat
          label="Current p95"
          value={p95 != null ? `${Math.round(p95).toLocaleString("en-US")} ms` : NO_VALUE}
          sub={p95 != null ? "internal budget 3 000 ms" : "no requests served yet"}
        />
        <Stat
          label="Degradation rate"
          value={stats.n ? fmtPct(stats.degraded / stats.n) : NO_VALUE}
          sub="calls missing at least one signal"
          tone="review"
        />
      </div>

      <div className="section-head">
        <span>Processed calls · {shell.connectionMode === "WS" ? "websocket stream" : shell.connectionMode.toLowerCase()}</span>
        <span className="hint">Click a row to open call detail</span>
      </div>

      {items.length === 0 ? (
        <div className="monitor-empty">
          <div className="headline">No calls processed yet</div>
          <div className="empty-body">
            {TRANSPORT_SENTENCE[shell.connectionMode]} Rows appear here as calls are scored — post a
            WAV to <code>POST /detect</code>, or use the Demo tab to put one through by hand.
          </div>
        </div>
      ) : (
        <div className="call-table" role="table" aria-label="Processed calls">
          <div className="call-head" role="row">
            <div role="columnheader">Call ID</div>
            <div role="columnheader" className="cell-num">Duration</div>
            <div role="columnheader">Verdict</div>
            <div role="columnheader" className="cell-num">Confidence</div>
            <div role="columnheader" className="cell-num">Latency</div>
            <div role="columnheader">Signals</div>
            <div role="columnheader" className="cell-num">Time</div>
          </div>

          {items.map((a) => {
            const key = verdictOf(a.verdict.is_synthetic, a.verdict.confidence);
            const tone = toneFor(key);
            const open = () => navigate(`/calls/${encodeURIComponent(a.id)}`);
            return (
              <div
                key={a.id}
                data-testid="call-row"
                role="row"
                tabIndex={0}
                onClick={open}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    open();
                  }
                }}
                className="call-row"
              >
                <div role="cell" className="cell-id" title={a.id}>{abbreviate(a.id)}</div>
                <div role="cell" className="cell-mono cell-num">{mmss(a.meta.duration_s)}</div>
                <div role="cell" className="cell-verdict">
                  <span className="verdict-dot" style={{ background: tone.color }} />
                  <span className="verdict-label" style={{ color: tone.color }}>{tone.label}</span>
                </div>
                <div role="cell" className="cell-confidence" style={{ color: tone.color }}>
                  {a.verdict.confidence.toFixed(2)}
                </div>
                <div role="cell" className="cell-mono cell-num">{Math.round(a.timings_ms.total)} ms</div>
                <div role="cell" className="cell-signals">
                  <span className={`pip ${a.signals.behavioral != null ? "on" : "off"}`} title="behavioral" />
                  <span
                    className={`pip ${a.signals.semantic != null ? "on" : "off"}`}
                    title={a.signals.semantic != null ? "semantic" : "semantic degraded"}
                  />
                  <span
                    className={`pip ${a.signals.acoustic != null ? "on" : "off"}`}
                    title={a.signals.acoustic != null ? "acoustic" : "acoustic degraded"}
                  />
                </div>
                <div role="cell" className="cell-mono cell-num">{fmtClock(a.ts)}</div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
