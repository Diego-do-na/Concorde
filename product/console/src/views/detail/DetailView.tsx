import React, { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Feed, Analysis, getFeedAnalysis } from "../../lib/api";
import { mmss, ms, verdictOf, toneFor, tokens } from "../../theme/tokens";
import DualChannelWaveform, { ScrubState } from "./waveform/DualChannelWaveform";
import ConfidenceTrace from "./trace/ConfidenceTrace";
import SignalBreakdown from "./signals/SignalBreakdown";
import Factors from "./factors/Factors";
import EventLog from "./markers/EventLog";
import { MarkerOverlay } from "./markers";
import { createTimeScale } from "./waveform/TimeScale";
import { useElementWidth } from "./waveform/useElementWidth";

type Props = { feed?: Feed };

export default function DetailView({ feed }: Props) {
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();
  const feedInstance = useMemo(() => feed ?? new Feed(), [feed]);
  const [items, setItems] = useState<Analysis[]>([]);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [retainedOnly, setRetainedOnly] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const unsub = feedInstance.subscribe((it) => {
      setItems(it);
    });
    return () => {
      unsub();
      if (!feed) feedInstance.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedInstance]);

  // fetch analysis when route id changes
  useEffect(() => {
    if (!id) {
      setAnalysis(null);
      setRetainedOnly(false);
      setLoading(false);
      return;
    }
    let alive = true;
    setLoading(true);
    getFeedAnalysis(id)
      .then((a) => {
        if (!alive) return;
        if (a) {
          setAnalysis(a);
          setRetainedOnly(false);
        } else {
          // not retained — show feed row only
          setAnalysis(null);
          setRetainedOnly(true);
        }
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
  }, [id]);

  const selectedId = id ?? null;

  const selectedFeedRow = items.find((i) => i.id === selectedId) ?? null;

  const [plotRef, plotWidth] = useElementWidth<HTMLDivElement>();
  const scale = useMemo(() => createTimeScale((analysis?.meta?.duration_s ?? selectedFeedRow?.meta?.duration_s) ?? 1, plotWidth), [analysis, selectedFeedRow, plotWidth]);

  const handleRowClick = (rowId: string) => {
    navigate(`/calls/${encodeURIComponent(rowId)}`);
  };

  // header pieces (from either analysis or retained-only row)
  const headerVerdict = analysis?.verdict ?? selectedFeedRow?.verdict;
  const headerMeta = analysis?.meta ?? selectedFeedRow?.meta;

  return (
    <div style={{ display: "flex", gap: 12, padding: 12 }}>
      {/* Sidebar */}
      <aside style={{ flex: "1 1 200px", minWidth: 200, maxWidth: 420, overflowY: "auto", background: tokens.surfaces.panelAlt }}>
        <div style={{ padding: 8, fontSize: 13, color: tokens.ink.INK2 }}>RECENT CALLS</div>
        {items.map((it) => {
          const tone = toneFor(verdictOf(it.verdict.is_synthetic, it.verdict.confidence) as any);
          const abbrev = it.id.length > 12 ? `${it.id.slice(0, 8)}…${it.id.slice(-4)}` : it.id;
          const isSelected = it.id === selectedId;
          return (
            <div
              key={it.id}
              data-testid="recent-row"
              onClick={() => handleRowClick(it.id)}
              style={{
                display: "flex",
                gap: 10,
                padding: "8px 10px",
                alignItems: "center",
                cursor: "pointer",
                background: isSelected ? tokens.surfaces.hover : undefined,
              }}
            >
              <div style={{ width: 3, height: 36, background: tone.color, borderRadius: 2 }} />
              <div style={{ flex: 1 }}>
                <div style={{ fontFamily: tokens.fonts.mono, fontSize: tokens.type.metricMono }}>{abbrev}</div>
                <div style={{ fontSize: tokens.type.label, color: tokens.ink.DIM_LABEL }}>
                  {tone.label} · {it.verdict.confidence.toFixed(2)}
                </div>
              </div>
            </div>
          );
        })}
        {items.length === 0 && <div style={{ padding: 12 }}>No recent calls</div>}
      </aside>

      {/* Main panel */}
      <section style={{ flex: "100 1 400px", padding: tokens.spacing.PANEL, background: tokens.surfaces.panel }}>
        {!selectedId ? (
          <div style={{ padding: 40, color: tokens.ink.MUTED }}>Select a call</div>
        ) : loading ? (
          <div data-testid="loading">Loading analysis…</div>
        ) : retainedOnly && !analysis ? (
          <div data-testid="retained-only">
            <div style={{ fontFamily: tokens.fonts.mono, fontSize: tokens.type.metricMono }}>{selectedFeedRow?.id}</div>
            <div style={{ color: tokens.ink.MUTED, marginTop: 6 }}>Analysis not retained — showing feed row only</div>
          </div>
        ) : analysis ? (
          <>
            {/* header metrics */}
            <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12 }}>
              <div style={{ fontFamily: tokens.fonts.mono, fontSize: tokens.type.metricMono }}>{analysis.id}</div>
              <div style={{ padding: "6px 10px", borderRadius: 4, background: analysis.verdict.is_synthetic ? tokens.semantic.SYNTHETIC.WASH : tokens.semantic.VERIFIED.WASH, color: tokens.ink.INK }}>
                {toneFor(verdictOf(analysis.verdict.is_synthetic, analysis.verdict.confidence) as any).label}
              </div>
              <div style={{ fontFamily: tokens.fonts.mono }}>{analysis.verdict.confidence.toFixed(3)}</div>
              <div style={{ color: tokens.ink.DIM_LABEL }}>threshold {analysis.verdict.threshold.toFixed(2)}</div>
              <div style={{ color: tokens.ink.DIM_LABEL }}>duration {mmss(analysis.meta.duration_s)}</div>
              <div style={{ color: tokens.ink.DIM_LABEL }}>latency {ms(analysis.timings_ms.total)}</div>
            </div>

            {/* plot container */}
            <div ref={plotRef} style={{ background: tokens.surfaces.plot, padding: 8, borderRadius: 6, border: `1px solid ${tokens.lines.line}`, marginBottom: 12 }}>
              <DualChannelWaveform analysis={analysis} />
              <ConfidenceTrace analysis={analysis} />
              <div style={{ position: "relative", marginTop: 6 }}>
                <MarkerOverlay events={analysis.events as any} timeScale={scale as any} />
              </div>
            </div>

            {/* two-column grid */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 420px", gap: "12px", marginBottom: 12 }}>
              <div>
                <Factors analysis={analysis} />
              </div>
              <div>
                <SignalBreakdown analysis={analysis} />
              </div>
            </div>

            <div>
              <h4 style={{ margin: "8px 0" }}>EVENT LOG</h4>
              <EventLog events={analysis.events as any} />
            </div>
          </>
        ) : (
          <div data-testid="no-data">No data</div>
        )}
      </section>
    </div>
  );
}

