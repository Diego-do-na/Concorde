import React from "react";
import { useShell } from "./shellContext";
import "./header.css";

const Chip: React.FC<{label: string, mono?: boolean}> = ({label, mono}) => (
  <div className="chip" role="listitem">
    <span className={mono ? "mono" : ""}>{label}</span>
  </div>
);

function fmtUptime(seconds: number) {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  if (h < 24) return `${h}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

export default function Header() {
  const { health, apiOnline, connectionMode } = useShell();

  const model = health?.model ?? "—";
  const contract = health?.contract ?? "—";
  const p95 = health && typeof health.p95_ms === "number" ? `${Math.round(health.p95_ms)}ms` : "—";
  // `${hours}h` reads "UP 0h" for the first hour of a service's life, which
  // is exactly when someone is most likely to be looking at it.
  const uptime = health ? fmtUptime(health.uptime_s || 0) : "—";

  return (
    <header className="shell-header">
      <div className="brand-row">
        <div className="wordmark">
          <div className="brand">CONCORDE</div>
          <div className="product">CONSOLE</div>
        </div>
        <div className="left-chips" role="list">
          {/* The dot answers "is the service up?", which is /health — not
              "how is the feed arriving?", which is the chip on the right. */}
          <div className="health">
            <span className={`health-dot ${apiOnline ? "online" : "offline"}`} />
            <span className="health-label">{apiOnline ? "OK" : "DEGRADED"}</span>
          </div>
          <Chip label={`MODEL ${model}`} mono />
          <Chip label={`CONTRACT ${contract}`} mono />
          <Chip label={`p95 ${p95}`} mono />
          <Chip label={`UP ${uptime}`} mono />
        </div>
        <div className="right-chips" role="list">
          <div className={`dep-chip ${connectionMode === "OFFLINE" ? "degraded" : ""}`}>
            <span className={`dot ${connectionMode === "WS" ? "green" : connectionMode === "POLLING" ? "blue" : "gray"}`} />
            <span className="label">feed</span>
            <span className="state">{connectionMode}</span>
          </div>
        </div>
      </div>
    </header>
  )
}

