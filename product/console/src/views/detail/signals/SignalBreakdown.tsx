import React from "react";
import { tokens, DEGRADED } from "../../../theme/tokens";

type Analysis = {
  signals: Record<string, any>;
  degraded?: Record<string, boolean>;
};

const ROWS: { key: string; label: string }[] = [
  { key: "behavioral", label: "Behavioral" },
  { key: "semantic", label: "Semantic" },
  { key: "acoustic", label: "Acoustic" },
];

function isAvailable(analysis: Analysis, key: string) {
  const flag = analysis.degraded ? (analysis.degraded as any)[`${key}_available`] : undefined;
  // if flag is explicitly false → degraded; if undefined assume available unless signal is null
  return !(flag === false);
}

export default function SignalBreakdown({ analysis }: { analysis: Analysis }) {
  const anyShed = ROWS.some((r) => !isAvailable(analysis, r.key));

  return (
    <div style={{ padding: 12, width: 360, fontFamily: tokens.fonts.body, color: tokens.ink.INK }}>
      {ROWS.map((r) => {
        const s = analysis.signals?.[r.key];
        const available = isAvailable(analysis, r.key) && s != null;
        const rawValue = !available
          ? null
          : typeof s === "number"
            ? s
            : typeof (s as any)?.value === "number"
              ? (s as any).value
              : typeof (s as any)?.confidence === "number"
                ? (s as any).confidence
                : null;

        const isDegraded = !available || rawValue == null;

        const fillWidth = isDegraded ? "0%" : `${Math.max(0, Math.min(1, rawValue)) * 100}%`;
        const fillColor = !isDegraded && rawValue! >= 0.5 ? "#ff4d4d" : "#3bff7a";

        return (
          <div key={r.key} style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 10 }}>
            <div style={{ width: 110, fontSize: tokens.type.body, color: tokens.ink.DIM_LABEL }}>{r.label}</div>
            <div style={{ flex: 1 }}>
              <div
                aria-label={`${r.key}-track`}
                style={{
                  height: 12,
                  borderRadius: 2,
                  background: isDegraded ? DEGRADED.HATCH : "oklch(0.26 0.015 252)",
                  position: "relative",
                  overflow: "hidden",
                }}
              >
                {/* filled portion */}
                <div
                  data-testid={`${r.key}-fill`}
                  style={{
                    position: "absolute",
                    left: 0,
                    top: 0,
                    bottom: 0,
                    width: fillWidth,
                    background: isDegraded ? "transparent" : fillColor,
                    transition: "width 160ms linear",
                  }}
                />
              </div>
            </div>
            <div style={{ width: 90, textAlign: "right", fontFamily: tokens.fonts.mono, fontSize: tokens.type.body, color: isDegraded ? tokens.ink.MUTED : tokens.ink.INK }}>
              {isDegraded ? (
                <span style={{ fontSize: tokens.type.label, letterSpacing: "0.06em", color: tokens.ink.MUTED }}>{DEGRADED.LABEL}</span>
              ) : (
                tokens.f2(rawValue!)
              )}
            </div>
          </div>
        );
      })}

      {anyShed ? (
        <div style={{ marginTop: 8, fontSize: tokens.type.label, color: tokens.ink.MUTED }}>
          signal shed — verdict is behavioral-only
        </div>
      ) : null}
    </div>
  );
}

