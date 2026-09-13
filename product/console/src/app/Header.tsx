import React from "react";
import { useShell } from "./shellContext";
import "./header.css";

const Chip: React.FC<{label: string, mono?: boolean}> = ({label, mono}) => (
  <div className="chip" role="listitem">
    <span className={mono ? "mono" : ""}>{label}</span>
  </div>
);

export default function Header() {
  const { health, connectionMode, lastKnownAt } = useShell();

  const model = health?.model ?? "—";
  const contract = health?.contract ?? "—";
  const p95 = health && typeof health.p95_ms === "number" ? `${Math.round(health.p95_ms)}ms` : "—";
  const uptime = health ? `${Math.round((health.uptime_s||0)/3600)}h` : "—";

  return (
    <header className="shell-header">
      <div className="brand-row">
        <div className="wordmark">
          <div className="brand">CONCORDE</div>
          <div className="product">CONSOLE</div>
        </div>
        <div className="left-chips" role="list">
          <div className="health">
            <span className={`health-dot ${connectionMode === "OFFLINE" ? "offline" : "online"}`} />
            <span className="health-label">{connectionMode === "OFFLINE" ? "DEGRADED" : "OK"}</span>
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

