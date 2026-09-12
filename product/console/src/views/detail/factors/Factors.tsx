import React from "react";
import { tokens, DEGRADED } from "../../../theme/tokens";
import { FEATURE_NOTES } from "./featureNotes";

type TopFactor = { feature: string; value: number | string; direction: "synthetic" | "human"; weight: number };
type Analysis = {
  top_factors: TopFactor[];
  rationale?: string;
  timings_ms?: { decode: number; vad: number; features: number; semantic: number | null; inference: number; total: number };
  meta?: { model_version?: string; git_sha?: string; feature_contract?: string; caller_turns?: number; duration_s?: number };
};

function DirectionLabel({ dir }: { dir: "synthetic" | "human" }) {
  const color = dir === "synthetic" ? tokens.semantic.SYNTHETIC.color : tokens.semantic.VERIFIED.color;
  const label = dir === "synthetic" ? "SYNTHETIC" : "HUMAN";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{ width: 10, height: 10, borderRadius: 6, background: color }} />
      <div style={{ fontSize: tokens.type.label, color }}>{label}</div>
    </div>
  );
}

export default function Factors({ analysis }: { analysis: Analysis }) {
  const factors = analysis.top_factors || [];
  const maxWeight = factors.reduce((m, f) => Math.max(m, f.weight ?? 0), 0) || 1;

  return (
    <div style={{ padding: 12, fontFamily: tokens.fonts.body, color: tokens.ink.INK, width: 900 }}>
      <h3 style={{ marginTop: 0 }}>TOP CONTRIBUTING FACTORS · MODEL FEATURE IMPORTANCE (FR-009)</h3>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(150px,1.6fr) 64px 88px minmax(60px,1fr)",
          gap: 12,
          alignItems: "center",
          marginBottom: 8,
        }}
      >
        {factors.map((f, i) => {
          const note = FEATURE_NOTES[f.feature] || { key: f.feature, note: "" };
          const weightPct = Math.round((f.weight / maxWeight) * 100);
          return (
            <React.Fragment key={f.feature + i}>
              <div>
                <div style={{ fontFamily: tokens.fonts.mono, fontSize: tokens.type.body }}>{note.key}</div>
                <div style={{ fontSize: 11, color: tokens.ink.INK2 }}>{`${f.feature} — ${note.note}`}</div>
              </div>

              <div style={{ textAlign: "right", fontFamily: tokens.fonts.mono }}>{String(f.value)}</div>

              <div>
                <DirectionLabel dir={f.direction} />
              </div>

              <div>
                <div style={{ height: 12, background: "oklch(0.26 0.015 252)", borderRadius: 3, position: "relative", overflow: "hidden" }}>
                  <div
                    data-testid={`weight-bar-${i}`}
                    style={{
                      position: "absolute",
                      left: 0,
                      top: 0,
                      bottom: 0,
                      width: `${weightPct}%`,
                      background: "linear-gradient(90deg,#4fb3ff,#3bff7a)",
                    }}
                  />
                </div>
                <div style={{ marginTop: 6, fontSize: tokens.type.label, color: tokens.ink.MUTED }}>{(f.weight).toFixed(3)}</div>
              </div>
            </React.Fragment>
          );
        })}
      </div>

      <div style={{ marginTop: 10, fontSize: 13, color: tokens.ink.INK2 }}>{analysis.rationale}</div>

      <div style={{ marginTop: 12 }}>
        <div style={{ fontSize: tokens.type.label, color: tokens.ink.DIM_LABEL, marginBottom: 6 }}>STAGE TIMINGS (ms)</div>
        <div style={{ display: "flex", gap: 12, alignItems: "flex-end" }}>
          {["decode", "vad", "features", "semantic", "inference"].map((k) => {
            const v = (analysis.timings_ms as any)?.[k];
            const isSemantic = k === "semantic";
            const isDegraded = isSemantic && v == null;
            const height = v == null ? 6 : Math.max(6, Math.min(120, (v / ((analysis.timings_ms?.total || 1) / 120)) ));
            return (
              <div key={k} style={{ width: 80, textAlign: "center" }}>
                <div
                  data-testid={`timing-${k}`}
                  style={{
                    height,
                    background: isDegraded ? DEGRADED.HATCH : "oklch(0.3 0.02 252)",
                    borderRadius: 3,
                    marginBottom: 6,
                  }}
                />
                <div style={{ fontSize: tokens.type.label, color: isDegraded ? tokens.ink.MUTED : tokens.ink.INK }}>{v == null ? "—" : String(v)}</div>
              </div>
            );
          })}
          <div style={{ marginLeft: 10, fontFamily: tokens.fonts.mono, color: tokens.ink.INK }}>Total: {analysis.timings_ms?.total ?? "—"}</div>
        </div>
      </div>

      <div style={{ marginTop: 12, fontFamily: tokens.fonts.mono, fontSize: tokens.type.body }}>
        <div>MODEL: {analysis.meta?.model_version ?? "—"}</div>
        <div>CONTRACT: {analysis.meta?.feature_contract ?? "—"}</div>
        <div>GIT SHA: {analysis.meta?.git_sha ?? "—"}</div>
        <div>CALLER TURNS: {analysis.meta?.caller_turns ?? "—"}</div>
        <div>DURATION: {analysis.meta?.duration_s ?? "—"}s</div>
      </div>
    </div>
  );
}

