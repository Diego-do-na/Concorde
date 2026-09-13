import React from 'react';
import metrics from './metrics.json';
import './exec.css';

export function computeBusiness({
  monthlyVolume,
  shareScreened,
  fraudRate,
  detectionRecall,
  lossPerIncident,
  inferenceCostPerCall
}: {
  monthlyVolume: number;
  shareScreened: number;
  fraudRate: number;
  detectionRecall: number;
  lossPerIncident: number;
  inferenceCostPerCall: number;
}) {
  const attemptsPerMonth = monthlyVolume * shareScreened * fraudRate;
  const attemptsCaught = attemptsPerMonth * detectionRecall;
  const exposureAvoided = attemptsCaught * lossPerIncident;
  const computeCostPerMonth = monthlyVolume * inferenceCostPerCall;
  return {
    attemptsPerMonth,
    attemptsCaught,
    exposureAvoided,
    computeCostPerMonth
  };
}

export default function ExecView() {
  const m = metrics as any;

  const inputs = {
    monthlyVolume: 150_000_000,
    shareScreened: 1.0,
    fraudRate: 0.0004,
    detectionRecall: m.validation?.detection_recall ?? 0.0,
    lossPerIncident: 1850,
    inferenceCostPerCall: 0.00001
  };

  const derived = computeBusiness(inputs);

  return (
    <div className="exec-root">
      <div className="banner">EVERY FIGURE BELOW IS A LABELLED ESTIMATE</div>

      <div className="stats-strip">
        <div className="stat">
          <div className="label">CALLS PROCESSED · 24 H</div>
          <div className="value">{m.processed_24h}</div>
        </div>
        <div className="stat">
          <div className="label">SYNTHETIC FLAGGED</div>
          <div className="value">{m.synthetic_flagged}</div>
        </div>
        <div className="stat">
          <div className="label">p95 LATENCY</div>
          <div className={`value latency ${m.latency_ms.p95 > 3000 ? 'over' : ''}`}>
            {m.latency_ms.p95} ms
            <div className="sub">Judge ceiling 30 000 ms, internal target 3 000 ms</div>
          </div>
        </div>
        <div className="stat">
          <div className="label">DEGRADATION RATE</div>
          <div className="value">{(m.degradation_rate * 100).toFixed(2)}%</div>
        </div>
        <div className="stat">
          <div className="label">VAL ROC-AUC</div>
          <div className="value">{m.validation?.roc_auc ?? '—'} <span className="tag measured">MEASURED</span></div>
        </div>
      </div>

      <section className="business-case">
        <h2 className="hero">{`$${Math.round(derived.exposureAvoided).toLocaleString()}`}</h2>
        <table>
          <thead>
            <tr><th>INPUT / DERIVED</th><th>VALUE</th><th>WHERE IT COMES FROM</th><th>TAG</th></tr>
          </thead>
          <tbody>
            <tr><td>Monthly call volume</td><td>{inputs.monthlyVolume.toLocaleString()}</td><td>Altur brief</td><td>STATED</td></tr>
            <tr><td>Share screened</td><td>{(inputs.shareScreened * 100).toFixed(0)}%</td><td>Assumed</td><td>ESTIMATE</td></tr>
            <tr><td>Voice-fraud attempt rate</td><td>{(inputs.fraudRate * 100).toFixed(3)}%</td><td>Industry</td><td>ESTIMATE</td></tr>
            <tr><td>Attempts per month</td><td>{Math.round(derived.attemptsPerMonth).toLocaleString()}</td><td>DERIVED</td><td>DERIVED</td></tr>
            <tr><td>Detection recall at threshold</td><td>{(inputs.detectionRecall * 100).toFixed(2)}%</td><td>metrics.json</td><td>MEASURED</td></tr>
            <tr><td>Attempts caught</td><td>{Math.round(derived.attemptsCaught).toLocaleString()}</td><td>DERIVED</td><td>DERIVED</td></tr>
            <tr><td>Loss per incident</td><td>${inputs.lossPerIncident.toLocaleString()}</td><td>Estimate</td><td>ESTIMATE</td></tr>
            <tr><td>Exposure avoided</td><td>${Math.round(derived.exposureAvoided).toLocaleString()}</td><td>DERIVED</td><td>DERIVED</td></tr>
            <tr><td>Inference cost per call</td><td>${inputs.inferenceCostPerCall.toFixed(6)}</td><td>docs/latency-report.md</td><td>ESTIMATE</td></tr>
            <tr><td>Compute cost per month</td><td>${derived.computeCostPerMonth.toFixed(2)}</td><td>DERIVED</td><td>DERIVED</td></tr>
          </tbody>
        </table>
      </section>
    </div>
  );
}

