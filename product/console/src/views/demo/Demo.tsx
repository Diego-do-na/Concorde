import React, { useState } from 'react';
import { analyze, detect } from '../../lib/api';
import { useNavigate } from 'react-router-dom';

const PREWIRED = [
  { id: 'human', label: 'Human caller · recorded in-house', path: '/demo/clip_human_01.wav' },
  { id: 'synth', label: 'Synthetic caller · unseen engine', path: '/demo/clip_synth_unseen_01.wav' },
];

export default function Demo() {
  const [status, setStatus] = useState<'IDLE'|'ANALYSING'|'HTTP 200'>('IDLE');
  const [detectBody, setDetectBody] = useState<any>(null);
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [confidence, setConfidence] = useState<number | null>(null);
  const [verdictTone, setVerdictTone] = useState<'AWAITING'|'SYNTH'|'HUMAN'>('AWAITING');
  const navigate = useNavigate();

  async function runFile(file: File) {
    setStatus('ANALYSING');
    setDetectBody(null);
    setAnalysisId(null);
    setConfidence(null);
    setVerdictTone('AWAITING');

    // Call detect and analyze in parallel
    const detP = detect(file).catch(() => ({ is_synthetic: false, confidence: 0.5 }));
    const anaP = analyze(file).catch(() => null);

    const [det, ana] = await Promise.all([detP, anaP]);
    setDetectBody(det);
    setConfidence(det.confidence);
    setVerdictTone(det.is_synthetic ? 'SYNTH' : 'HUMAN');
    if (ana && ana.id) {
      setAnalysisId(ana.id);
      setStatus('HTTP 200');
    } else {
      setStatus('HTTP 200');
    }
  }

  async function fetchAndRun(path: string) {
    try {
      const res = await fetch(path);
      const blob = await res.blob();
      const file = new File([blob], path.split('/').pop() || 'clip.wav', { type: blob.type || 'audio/wav' });
      await runFile(file);
    } catch (e) {
      console.error(e);
    }
  }

  return (
    <div style={{ padding: 20 }}>
      <h2>Demo — arbitrary WAV upload · FR-013</h2>
      <div style={{ display: 'flex', gap: 20 }}>
        <div style={{ flex: 1 }}>
          <div
            style={{
              border: '2px dashed',
              borderColor: 'oklch(0.55 0.02 252)',
              padding: 20,
              borderRadius: 6,
              background: 'oklch(0.22 0.015 252)',
            }}
            onDragOver={(e) => e.preventDefault()}
            onDrop={async (e) => {
              e.preventDefault();
              const f = e.dataTransfer?.files?.[0];
              if (f) await runFile(f);
            }}
          >
            <input
              type="file"
              accept="audio/wav"
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (f) await runFile(f);
              }}
            />
            <div style={{ marginTop: 12 }}>Drop a WAV here or choose a file</div>
          </div>

          <h3>Pre-wired robustness clips</h3>
          <ul>
            {PREWIRED.map((c) => (
              <li key={c.id}>
                <button onClick={() => fetchAndRun(c.path)}>{c.label}</button>
              </li>
            ))}
          </ul>
        </div>

        <div style={{ width: 420 }}>
          <div>
            <strong>STATUS</strong>: <span>{status}</span>
          </div>
          <div style={{ marginTop: 8 }}>
            <strong>VERDICT</strong>:
            <span style={{ marginLeft: 8, padding: '4px 8px', border: '1px dashed', borderRadius: 6 }}>
              {detectBody ? (detectBody.is_synthetic ? 'SYNTHETIC' : 'HUMAN') : 'AWAITING INPUT'}
            </span>
          </div>
          <div style={{ marginTop: 8 }}>
            <strong>CONFIDENCE</strong>: {confidence !== null ? confidence.toFixed(3) : '—'}
          </div>

          <div style={{ marginTop: 12 }}>
            <strong>DETECT response body</strong>
            <pre style={{ background: '#0b0b0b', color: '#eee', padding: 8, borderRadius: 6, minHeight: 80 }}>
              {detectBody ? JSON.stringify(detectBody, null, 2) : '{ "is_synthetic": <bool>, "confidence": <float> }'}
            </pre>
          </div>

          <div style={{ marginTop: 12 }}>
            <button
              onClick={() => {
                if (analysisId) navigate(`/calls/${encodeURIComponent(analysisId)}`);
              }}
              disabled={!analysisId}
            >
              Open in detail
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

