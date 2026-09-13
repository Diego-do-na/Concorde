import React from "react";
import { DEGRADED } from "../../../theme/tokens";
import "./signals.css";

type Analysis = {
  signals: Record<string, any>;
  degraded?: Record<string, boolean>;
};

/**
 * Per-signal sub-scores (§8.2).
 *
 * The three rows are always rendered, in a fixed order, whether or not the
 * signal ran. A signal that did not run is hatched and labelled DEGRADED —
 * never a zero-width bar, which would read as "scored 0.00" and is exactly
 * the unmarked degradation §7.2 forbids.
 *
 * The bar fill used to be literal `#ff4d4d` / `#3bff7a`, and was tinted by
 * whether the score crossed 0.5. That is a threshold the model does not use
 * (the shipped one is 0.4198) and a colour pair from no palette, so the fill
 * is now a single neutral and the number carries the value.
 */

const ROWS: { key: string; label: string; note: string }[] = [
  { key: "behavioral", label: "Behavioral", note: "F-01…F-22 · turn-taking, latency, overlap" },
  { key: "semantic", label: "Semantic", note: "F-23 · invention of non-existent information" },
  { key: "acoustic", label: "Acoustic", note: "spectral extractor" },
];

/** Read a sub-score that may arrive as a number or as a wrapped object. */
function scoreOf(s: unknown): number | null {
  if (typeof s === "number") return s;
  if (s && typeof s === "object") {
    const o = s as Record<string, unknown>;
    if (typeof o.value === "number") return o.value;
    if (typeof o.confidence === "number") return o.confidence;
  }
  return null;
}

function isAvailable(analysis: Analysis, key: string) {
  const flag = analysis.degraded?.[`${key}_available`];
  // An explicit false means shed. Undefined falls back to "present unless
  // the signal itself is null".
  return flag !== false;
}

export default function SignalBreakdown({ analysis }: { analysis: Analysis }) {
  const shed = ROWS.filter((r) => scoreOf(analysis.signals?.[r.key]) == null || !isAvailable(analysis, r.key));

  return (
    <div className="signal-breakdown">
      {ROWS.map((r) => {
        const raw = isAvailable(analysis, r.key) ? scoreOf(analysis.signals?.[r.key]) : null;
        const degraded = raw == null;
        const width = degraded ? "0%" : `${Math.max(0, Math.min(1, raw)) * 100}%`;

        return (
          <div className="signal-row" key={r.key}>
            <div className="signal-top">
              <span className="signal-label">{r.label}</span>
              {degraded ? (
                <span className="signal-degraded">{DEGRADED.LABEL}</span>
              ) : (
                <span className="signal-score">{raw.toFixed(2)}</span>
              )}
            </div>
            <div
              className={`bar-track${degraded ? " is-degraded" : ""}`}
              aria-label={`${r.key}-track`}
            >
              {!degraded && <div data-testid={`${r.key}-fill`} className="bar-fill" style={{ width }} />}
            </div>
            <div className="signal-note">{r.note}</div>
          </div>
        );
      })}

      {shed.length > 0 ? (
        <p className="signal-shed">
          {shed.length === ROWS.length - 1 ? "Verdict is behavioral-only." : "One or more signals were shed."}{" "}
          A shed signal is excluded from the blend, not scored as zero.
        </p>
      ) : null}
    </div>
  );
}
