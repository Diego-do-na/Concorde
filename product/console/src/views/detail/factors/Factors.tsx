import React from "react";
import { toneFor } from "../../../theme/tokens";
import { FEATURE_NOTES } from "./featureNotes";
import "./factors.css";

type TopFactor = { feature: string; value: number | string; direction: "synthetic" | "human"; weight: number };
type Analysis = { top_factors: TopFactor[] };

/**
 * Top contributing factors (FR-009).
 *
 * This component used to also own the rationale, the stage timings and the
 * model metadata. Those moved to `CallMeta` in the right rail, where the
 * reference layout puts them — next to the signal breakdown they explain,
 * rather than below a table of numbers.
 *
 * `top_factors` is empty for a call read back from `/feed/analysis/:id`:
 * only `POST /analyze` computes importances. That is a real absence, so it
 * gets an explicit message rather than an empty table.
 */

/** The name the extractor emits, e.g. "resp_latency_cv" → "F-04". */
const ID_BY_KEY: Record<string, string> = Object.fromEntries(
  Object.entries(FEATURE_NOTES).map(([id, v]) => [v.key, id]),
);

function describe(feature: string) {
  // top_factors carries the extractor's own name ("resp_latency_cv"), but a
  // hand-built fixture may carry the contract id ("F-04"). Accept either.
  const byId = FEATURE_NOTES[feature];
  if (byId) return { id: feature, key: byId.key, note: byId.note };
  const id = ID_BY_KEY[feature];
  if (id) return { id, key: feature, note: FEATURE_NOTES[id].note };
  return { id: "", key: feature, note: "" };
}

export default function Factors({ analysis }: { analysis: Analysis }) {
  const factors = analysis.top_factors || [];

  if (factors.length === 0) {
    return (
      <div className="factors-empty" data-testid="no-factors">
        Feature importances are computed by <code>POST /analyze</code> and are not stored with the
        retained analysis, so they are unavailable for this call.
      </div>
    );
  }

  const maxWeight = factors.reduce((m, f) => Math.max(m, f.weight ?? 0), 0) || 1;

  return (
    <div className="factors">
      <div className="factors-head">
        <div>Feature</div>
        <div className="num">Value</div>
        <div>Direction</div>
        <div>Weight</div>
      </div>

      {factors.map((f, i) => {
        const d = describe(f.feature);
        const tone = toneFor(f.direction === "synthetic" ? "synthetic" : "verified");
        const weightPct = Math.round((f.weight / maxWeight) * 100);
        return (
          <div className="factor-row" key={`${f.feature}-${i}`}>
            <div className="factor-name">
              <div className="key">{d.key}</div>
              <div className="note">{d.id ? `${d.id} · ${d.note}` : d.note}</div>
            </div>

            <div className="factor-value num">
              {typeof f.value === "number" ? f.value.toFixed(2) : String(f.value)}
            </div>

            {/* Direction is a word plus an arrow glyph; the tint only
                reinforces it. */}
            <div className="factor-direction" style={{ color: tone.color }}>
              <span aria-hidden="true">{f.direction === "synthetic" ? "▲" : "▼"}</span>
              <span>{f.direction === "synthetic" ? "SYNTHETIC" : "HUMAN"}</span>
            </div>

            <div className="factor-weight">
              <div className="bar-track">
                <div
                  data-testid={`weight-bar-${i}`}
                  className="bar-fill"
                  style={{ width: `${weightPct}%`, background: tone.color }}
                />
              </div>
              <div className="weight-value">{f.weight.toFixed(2)}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
