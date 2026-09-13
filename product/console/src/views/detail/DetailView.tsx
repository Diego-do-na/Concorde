import React, { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { FeedLike, Analysis, getFeedAnalysis } from "../../lib/api";
import { useShell } from "../../app/shellContext";
import { mmss, verdictOf, toneFor } from "../../theme/tokens";
import DualChannelWaveform, { ScrubState } from "./waveform/DualChannelWaveform";
import ConfidenceTrace from "./trace/ConfidenceTrace";
import SignalBreakdown from "./signals/SignalBreakdown";
import Factors from "./factors/Factors";
import CallMeta from "./meta/CallMeta";
import EventLog from "./markers/EventLog";
import Legend from "./markers/Legend";
import { MarkerOverlay } from "./markers";
import { createTimeScale } from "./waveform/TimeScale";
import { useElementWidth } from "./waveform/useElementWidth";
import { interpolateConfidence } from "./waveform/confidence";
import AudioPlayer from "./audio/AudioPlayer";
import "./detail.css";

type Props = { feed?: FeedLike };

function abbreviate(id: string) {
  return id.length > 20 ? `${id.slice(0, 12)}…${id.slice(-4)}` : id;
}

/** A labelled figure in the call header row. */
function Field({ label, value, className }: { label: string; value: React.ReactNode; className?: string }) {
  return (
    <div className={className}>
      <div className="field-label">{label}</div>
      <div className="field-value">{value}</div>
    </div>
  );
}

export default function DetailView({ feed }: Props) {
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  // The shell owns the feed; this view only reads it. Constructing one here
  // meant the console held two websockets and two poll timers against the
  // same ring whenever the detail view was mounted.
  const shell = useShell();
  const feedInstance = feed ?? shell.feed;

  // A caller may hand over a full `/analyze` payload (the demo view does).
  // It is strictly richer than what `/feed/analysis/:id` can return, so it
  // wins — but only for the id it actually describes, so a stale entry from
  // the history stack cannot bleed onto a different call.
  const location = useLocation();
  const navState = location.state as { analysis?: Analysis; audioFile?: File | Blob } | null;
  const handedOver = navState?.analysis ?? null;
  // The only audio this view can ever play: the clip the demo route analysed
  // in this browser session and handed over alongside its payload. The
  // service never retains audio (NFR-011), so every other way into this view
  // renders the player's explicit "not available" state.
  const audioFile = handedOver && handedOver.id === id ? navState?.audioFile ?? null : null;

  const [items, setItems] = useState<Analysis[]>([]);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [retainedOnly, setRetainedOnly] = useState(false);
  const [loading, setLoading] = useState(false);

  // The waveform produces scrub state on hover and the trace consumes it to
  // move its playhead. Both sides existed before but were never connected,
  // so hovering a lane moved nothing on the confidence trace below it.
  const [scrub, setScrub] = useState<ScrubState | null>(null);

  useEffect(() => {
    return feedInstance.subscribe(setItems);
  }, [feedInstance]);

  useEffect(() => {
    if (!id) {
      setAnalysis(null);
      setRetainedOnly(false);
      setLoading(false);
      return;
    }
    setScrub(null);

    if (handedOver && handedOver.id === id) {
      setAnalysis(handedOver);
      setRetainedOnly(false);
      setLoading(false);
      return;
    }

    let alive = true;
    setLoading(true);
    getFeedAnalysis(id)
      .then((a) => {
        if (!alive) return;
        setAnalysis(a);
        setRetainedOnly(!a);
      })
      .catch(() => {
        if (!alive) return;
        setAnalysis(null);
        setRetainedOnly(true);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [id, handedOver]);

  const selectedId = id ?? null;
  const selectedFeedRow = items.find((i) => i.id === selectedId) ?? null;

  const [plotRef, plotWidth] = useElementWidth<HTMLDivElement>();
  const duration = analysis?.meta?.duration_s ?? selectedFeedRow?.meta?.duration_s ?? 1;
  const scale = useMemo(() => createTimeScale(duration, plotWidth), [duration, plotWidth]);

  const verdict = analysis?.verdict ?? selectedFeedRow?.verdict;
  const tone = verdict ? toneFor(verdictOf(verdict.is_synthetic, verdict.confidence)) : null;

  return (
    <div className="detail">
      <aside className="detail-col recent-rail">
        <div className="rail-head">Recent calls</div>
        {items.length === 0 ? (
          <div className="rail-empty">No recent calls</div>
        ) : (
          items.map((it) => {
            const t = toneFor(verdictOf(it.verdict.is_synthetic, it.verdict.confidence));
            const open = () => navigate(`/calls/${encodeURIComponent(it.id)}`);
            return (
              <div
                key={it.id}
                data-testid="recent-row"
                role="button"
                tabIndex={0}
                onClick={open}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    open();
                  }
                }}
                className={`recent-row${it.id === selectedId ? " is-selected" : ""}`}
              >
                <div className="recent-tone" style={{ background: t.color }} />
                <div className="recent-body">
                  <div className="recent-id" title={it.id}>{abbreviate(it.id)}</div>
                  <div className="recent-sub" style={{ color: t.color }}>
                    {t.label} · {it.verdict.confidence.toFixed(2)}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </aside>

      <section className="detail-col evidence">
        {!selectedId ? (
          <div className="detail-notice">
            <div className="headline">No call selected</div>
            Pick a call from the rail, or open one from the monitor.
          </div>
        ) : loading ? (
          <div className="detail-notice" data-testid="loading">
            <div className="headline">Loading analysis…</div>
          </div>
        ) : retainedOnly && !analysis ? (
          <div className="detail-notice" data-testid="retained-only">
            <div className="headline">{selectedFeedRow?.id ?? selectedId}</div>
            Analysis not retained — showing feed row only. The service keeps a bounded number of full
            analyses; this call has aged out of that window, so only its feed event survives.
          </div>
        ) : analysis ? (
          <>
            <div className="call-header">
              <Field className="call-id" label="Call" value={analysis.id} />
              {tone && (
                <div className="verdict-badge" style={{ color: tone.color, background: tone.wash }}>
                  <span className="dot" />
                  {tone.label}
                </div>
              )}
              <Field label="Confidence" value={<span style={{ color: tone?.color }}>{analysis.verdict.confidence.toFixed(2)}</span>} />
              <Field label="Threshold" value={analysis.verdict.threshold.toFixed(2)} />
              <Field label="Duration" value={mmss(analysis.meta.duration_s)} />
              <Field label="Process latency" value={`${Math.round(analysis.timings_ms.total)} ms`} />
            </div>

            <div className="panel-head">
              <span>Channel waveforms · event markers · confidence trace</span>
              <Legend events={analysis.events as any} />
            </div>

            {/* The trace goes in as a child so the shared time axis renders
                below every lane rather than between them, and the markers
                overlay the whole stack on the same TimeScale. */}
            <div className="plot-panel" ref={plotRef}>
              <DualChannelWaveform analysis={analysis} onScrub={setScrub}>
                <ConfidenceTrace analysis={analysis} scrub={scrub} />
              </DualChannelWaveform>
              <MarkerOverlay events={analysis.events as any} timeScale={scale as any} />
            </div>

            {/* Playback drives the same scrub state the hover does, so the
                confidence-trace playhead follows the audio. */}
            <AudioPlayer
              file={audioFile}
              durationS={analysis.meta.duration_s}
              onTime={(t) =>
                setScrub(t == null ? null : { t, confidence: interpolateConfidence(analysis.timeline, t) })
              }
            />

            <div className="panel-head" style={{ paddingTop: 22 }}>
              <span>Top contributing factors · model feature importance (FR-009)</span>
            </div>
            <Factors analysis={analysis} />
          </>
        ) : (
          <div className="detail-notice" data-testid="no-data">
            <div className="headline">No data</div>
          </div>
        )}
      </section>

      <aside className="detail-col interpretation">
        {analysis ? (
          <>
            <section className="rail-section">
              <div className="rail-section-head">Signal breakdown</div>
              <SignalBreakdown analysis={analysis} />
            </section>

            <CallMeta analysis={analysis} />

            <section className="rail-section">
              <div className="rail-section-head">Event log</div>
              <EventLog events={analysis.events as any} />
            </section>
          </>
        ) : null}
      </aside>
    </div>
  );
}
