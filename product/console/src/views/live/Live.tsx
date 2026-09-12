import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Feed, Analysis } from "../../lib/api";
import { mmss, verdictOf, toneFor } from "../../theme/tokens";

type Props = {
  feed?: Feed;
};

export default function Live({ feed }: Props) {
  const navigate = useNavigate();
  const feedInstance = useMemo(() => feed ?? new Feed(), [feed]);
  const [items, setItems] = useState<Analysis[]>([]);

  useEffect(() => {
    const unsub = feedInstance.subscribe((it) => {
      // prepend new items (feed returns recent in newest-first order)
      setItems((prev) => {
        // if identical arrays, replace
        if (JSON.stringify(prev.map((p) => p.id)) === JSON.stringify(it.map((p) => p.id))) return prev;
        return it;
      });
    });
    return () => {
      unsub();
      // close only if we created the feed (prop didn't pass one)
      if (!feed) feedInstance.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedInstance]);

  if (!items || items.length === 0) {
    return <div style={{ padding: 20 }}>No calls processed yet</div>;
  }

  return (
    <div style={{ padding: 12 }}>
      {/* Stats strip (minimal) */}
      <div style={{ display: "flex", gap: 12, marginBottom: 12, fontFamily: 'IBM Plex Mono, monospace', fontSize: 19 }}>
        <div>PROCESSED · 24 H</div>
        <div>SYNTHETIC RATE</div>
        <div>REVIEW QUEUE</div>
        <div>
          CURRENT p95 <span style={{ marginLeft: 6, fontSize: 11 }}>budget 3 000 ms</span>
        </div>
        <div>DEGRADATION RATE</div>
      </div>

      <div role="table" style={{ width: "100%", fontSize: 13 }}>
        <div style={{ display: "flex", gap: 12, padding: "6px 10px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
          <div style={{ width: 160, fontFamily: 'IBM Plex Mono, monospace' }}>CALL ID</div>
          <div style={{ width: 90 }}>DURATION</div>
          <div style={{ width: 140 }}>VERDICT</div>
          <div style={{ width: 110 }}>CONFIDENCE</div>
          <div style={{ width: 90 }}>LATENCY</div>
          <div style={{ width: 140 }}>SIGNALS</div>
          <div style={{ flex: 1 }}>TIME</div>
        </div>

        {items.map((a) => {
          const tone = toneFor(verdictOf(a.verdict.is_synthetic, a.verdict.confidence) as any);
          const abbrev = a.id.length > 12 ? `${a.id.slice(0, 8)}…${a.id.slice(-4)}` : a.id;
          return (
            <div
              key={a.id}
              data-testid="call-row"
              role="row"
              tabIndex={0}
              onClick={() => navigate(`/calls/${encodeURIComponent(a.id)}`)}
              style={{
                display: "flex",
                gap: 12,
                padding: "7px 12px",
                alignItems: "center",
                cursor: "pointer",
                borderBottom: "1px solid rgba(255,255,255,0.03)",
              }}
            >
              <div style={{ width: 160, fontFamily: 'IBM Plex Mono, monospace', color: "#fff" }}>{abbrev}</div>
              <div style={{ width: 90 }}>{mmss(a.meta.duration_s)}</div>
              <div style={{ width: 140, display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ width: 10, height: 10, borderRadius: 6, background: tone.color, display: "inline-block" }} />
                <span>{tone.label}</span>
              </div>
              <div style={{ width: 110 }}>{a.verdict.confidence.toFixed(2)}</div>
              <div style={{ width: 90 }}>{a.timings_ms.total} ms</div>
              <div style={{ width: 140, display: "flex", gap: 6 }}>
                {/* behavioral */}
                <div style={{ width: 14, height: 12, background: a.signals.behavioral ? "#3bff7a33" : "#ff4d4d33", borderRight: "2px solid #3bff7a55" }} />
                {/* semantic */}
                <div
                  title={a.signals.semantic ? "" : "semantic degraded"}
                  style={{
                    width: 14,
                    height: 12,
                    background: a.signals.semantic ? "#3bff7a33" : "repeating-linear-gradient(135deg,#00000011 0 3px,#00000000 3px 6px)",
                    borderRight: "2px solid #3bff7a55",
                  }}
                />
                {/* acoustic */}
                <div style={{ width: 14, height: 12, background: a.signals.acoustic ? "#3bff7a33" : "#ff4d4d33" }} />
              </div>
              <div style={{ flex: 1 }}>{new Date().toLocaleTimeString()}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

