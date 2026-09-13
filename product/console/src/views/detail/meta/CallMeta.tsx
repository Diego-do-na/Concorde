import React from "react";
import "./meta.css";

type Timings = {
  decode: number;
  vad: number;
  features: number;
  semantic: number | null;
  inference: number;
  total: number;
};

type Analysis = {
  rationale?: string;
  timings_ms?: Timings;
  meta?: { model_version?: string; git_sha?: string; feature_contract?: string; duration_s?: number };
};

/**
 * Rationale, per-stage timings and build metadata — the right rail's lower
 * half. Split out of `Factors`, which had grown to own three unrelated
 * panels.
 *
 * The stage bars are horizontal and share one scale (the total), so the eye
 * reads "semantic is most of the budget" directly. They were vertical bars
 * on independent heights before, which made the largest stage look like the
 * smallest whenever the total was dominated by one entry.
 */

const STAGES: { key: keyof Timings; label: string }[] = [
  { key: "decode", label: "Decode" },
  { key: "vad", label: "VAD" },
  { key: "features", label: "Features" },
  { key: "semantic", label: "Semantic" },
  { key: "inference", label: "Inference" },
];

export default function CallMeta({ analysis }: { analysis: Analysis }) {
  const t = analysis.timings_ms;
  const total = t?.total || 1;

  return (
    <>
      {analysis.rationale ? (
        <section className="rail-section">
          <div className="rail-section-head">Rationale</div>
          <p className="rationale">{analysis.rationale}</p>
        </section>
      ) : null}

      <section className="rail-section">
        <div className="rail-section-head">Stage timings</div>
        <div className="timings">
          {STAGES.map(({ key, label }) => {
            const v = t?.[key] as number | null | undefined;
            // null is "this stage did not run", which is not zero — it gets
            // the hatch and an em dash, never a 0 ms bar (§7.2).
            const degraded = v == null;
            const pct = degraded ? 100 : Math.max(1, Math.min(100, (v / total) * 100));
            return (
              <div className="timing-row" data-testid={`timing-${key}`} key={key}>
                <div className="timing-label">{label}</div>
                <div className={`bar-track${degraded ? " is-degraded" : ""}`}>
                  {!degraded && <div className="bar-fill" style={{ width: `${pct}%` }} />}
                </div>
                <div className={`timing-value${degraded ? " is-degraded" : ""}`}>
                  {degraded ? "—" : `${Math.round(v)} ms`}
                </div>
              </div>
            );
          })}
          <div className="timing-row is-total" data-testid="timing-total">
            <div className="timing-label">Total</div>
            <div className="bar-track">
              <div className="bar-fill" style={{ width: "100%" }} />
            </div>
            <div className="timing-value">{t ? `${Math.round(t.total)} ms` : "—"}</div>
          </div>
        </div>
      </section>

      <section className="rail-section">
        <div className="rail-section-head">Build</div>
        <dl className="meta-list">
          <div>
            <dt>Model</dt>
            <dd>{analysis.meta?.model_version || "—"}</dd>
          </div>
          <div>
            <dt>Contract</dt>
            <dd>{analysis.meta?.feature_contract || "—"}</dd>
          </div>
          <div>
            <dt>Git SHA</dt>
            <dd>{analysis.meta?.git_sha || "—"}</dd>
          </div>
        </dl>
      </section>
    </>
  );
}
