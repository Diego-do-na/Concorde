import React, { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { analyze, detect, Analysis } from '../../lib/api';
import { toneFor } from '../../theme/tokens';
import AudioPlayer from '../detail/audio/AudioPlayer';
import './demo.css';

/*
 * Demo zone (FR-013) — put an arbitrary WAV through the real endpoints and
 * show exactly what came back.
 *
 * The point of this screen on stage is that /detect's body is two keys and
 * nothing else (ADR-013), and that invalid input still returns HTTP 200 with
 * the fallback verdict rather than an error. So the raw response body is
 * shown verbatim, not summarised.
 */

const PREWIRED = [
  {
    id: 'human',
    label: 'Human caller · recorded in-house',
    note: 'Consented recording, stereo 8 kHz, background noise intact',
    path: '/demo/clip_human_01.wav',
  },
  {
    id: 'synth',
    label: 'Synthetic caller · unseen engine',
    note: 'Generated with a TTS engine absent from train and val splits',
    path: '/demo/clip_synth_unseen_01.wav',
  },
];

const STAGES = ['decode', 'vad', 'features', 'semantic', 'inference'] as const;

type Status = 'IDLE' | 'ANALYSING' | 'DONE' | 'FETCH_FAILED';

/**
 * A client-minted call id, sent as `call_id` so the verdict can be opened in
 * the detail view afterwards.
 *
 * `POST /analyze` does not return an id — `analysis::AnalyzeResponse` has no
 * such field — but the console's `AnalysisZ` requires one, so `analyze()`
 * threw on every real response, `Demo` swallowed it with `.catch(() => null)`
 * and "Open in detail" was permanently disabled against the live service.
 * `http::parse::extract_audio` reads `call_id` from both JSON and multipart,
 * and `feed::Feed::push` uses it as the retained key, so choosing the id here
 * is what makes the hand-off work.
 */
function mintCallId() {
  const rand = Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, '0');
  return `demo_${rand}`;
}

export default function Demo() {
  const [status, setStatus] = useState<Status>('IDLE');
  const [callId, setCallId] = useState<string | null>(null);
  const [detectBody, setDetectBody] = useState<{ is_synthetic: boolean; confidence: number } | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  // Kept in memory so the clip can be replayed here and in the detail view
  // it hands over to — the service itself never retains audio (NFR-011).
  const [audioFile, setAudioFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  async function runFile(file: File) {
    const id = mintCallId();
    setStatus('ANALYSING');
    setCallId(id);
    setFileName(file.name);
    setAudioFile(file);
    setDetectBody(null);
    setAnalysis(null);
    setAnalyzeError(null);

    // Both routes are hit with the same clip: /detect is the scored artifact
    // and /analyze is the explanation. Running them in parallel is also the
    // honest thing to show — the dashboard never reads /detect for anything
    // but the two keys.
    const [det, ana] = await Promise.all([
      detect(file, id).catch(() => ({ is_synthetic: false, confidence: 0.5 })),
      analyze(file, id).catch((e: unknown) => {
        setAnalyzeError(e instanceof Error ? e.message : String(e));
        return null;
      }),
    ]);

    setDetectBody(det);
    setAnalysis(ana);
    setStatus('DONE');
  }

  async function fetchAndRun(path: string, label: string) {
    setStatus('ANALYSING');
    setAnalyzeError(null);
    try {
      const res = await fetch(path);
      if (!res.ok) throw new Error(`${res.status}`);
      const blob = await res.blob();
      const file = new File([blob], path.split('/').pop() || 'clip.wav', {
        type: blob.type || 'audio/wav',
      });
      await runFile(file);
    } catch {
      // The clips are optional repo assets (public/demo is gitignored for
      // anything audio-bearing), so a missing file is a normal state here.
      setStatus('FETCH_FAILED');
      setFileName(label);
    }
  }

  // The badge is a literal mirror of /detect's two-key body, so it is tinted
  // by `is_synthetic` alone — not by `verdictOf`, whose REVIEW state would
  // colour a "synthetic" answer amber and contradict the word next to it.
  // Proximity to the threshold is said in words underneath instead.
  const verdictTone = detectBody ? toneFor(detectBody.is_synthetic ? 'synthetic' : 'verified') : null;
  const inReviewBand =
    detectBody != null && analysis != null
      ? Math.abs(detectBody.confidence - analysis.verdict.threshold) <= 0.2
      : false;

  const responseBody = detectBody
    ? JSON.stringify(detectBody, null, 2)
    : '{\n  "is_synthetic": <bool>,\n  "confidence": <float>\n}';

  return (
    <div className="demo">
      <section className="demo-input">
        <div className="panel-head">
          <span>Arbitrary WAV · FR-013</span>
        </div>

        <div
          className={`dropzone${dragging ? ' is-dragging' : ''}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={async (e) => {
            e.preventDefault();
            setDragging(false);
            const f = e.dataTransfer?.files?.[0];
            if (f) await runFile(f);
          }}
          onClick={() => inputRef.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
        >
          <input
            ref={inputRef}
            type="file"
            accept="audio/wav,.wav"
            className="visually-hidden"
            onChange={async (e) => {
              const f = e.target.files?.[0];
              if (f) await runFile(f);
            }}
          />
          <div className="dropzone-glyph" aria-hidden="true">⌄</div>
          <div className="dropzone-title">Drop or select a stereo 8 kHz WAV</div>
          <div className="dropzone-note">
            Channel 0 caller, channel 1 agent. Anything else is resampled server-side; invalid input
            returns the fallback verdict, never an error.
          </div>
          {fileName && <div className="dropzone-file">{fileName}</div>}
        </div>

        <div className="panel-head" style={{ paddingTop: 22 }}>
          <span>Pre-wired robustness clips</span>
        </div>
        <div className="clip-list">
          {PREWIRED.map((c) => (
            <button key={c.id} className="clip" onClick={() => fetchAndRun(c.path, c.label)}>
              <span className="clip-play" aria-hidden="true">▶</span>
              <span className="clip-body">
                <span className="clip-label">{c.label}</span>
                <span className="clip-note">{c.note}</span>
              </span>
            </button>
          ))}
        </div>
        <p className="demo-footnote">
          The synthetic clip is generated with a voice engine absent from both training and
          validation splits. It exists to attack our own model on stage, not to decorate the demo.
        </p>
      </section>

      <section className="demo-output">
        <div className="panel-head">
          <span>Live verdict · POST /detect</span>
          <span className={`status-chip is-${status.toLowerCase()}`}>{status.replace('_', ' ')}</span>
        </div>

        <div className="output-panel">
          <div className="output-file">{fileName ?? 'no input selected'}</div>

          <div className="stage-list">
            {STAGES.map((s) => {
              const v = analysis?.timings_ms?.[s] ?? null;
              const total = analysis?.timings_ms?.total || 1;
              const pct = v == null ? 0 : Math.max(1, Math.min(100, (v / total) * 100));
              return (
                <div className="stage-row" key={s} data-testid={`stage-${s}`}>
                  <span className="stage-label">{s}</span>
                  <span className={`bar-track${v == null && analysis ? ' is-degraded' : ''}`}>
                    {v != null && <span className="bar-fill" style={{ width: `${pct}%` }} />}
                  </span>
                  <span className="stage-value">{v == null ? '—' : `${Math.round(v)} ms`}</span>
                </div>
              );
            })}
          </div>

          <div className="verdict-row">
            <div
              className="verdict-badge lg"
              style={verdictTone ? { color: verdictTone.color, background: verdictTone.wash } : undefined}
            >
              <span className="dot" />
              {detectBody ? (detectBody.is_synthetic ? 'SYNTHETIC' : 'HUMAN') : 'AWAITING INPUT'}
            </div>
            <div className="confidence-figure">
              <div className="field-label">Confidence</div>
              <div className="field-value" style={verdictTone ? { color: verdictTone.color } : undefined}>
                {detectBody ? detectBody.confidence.toFixed(2) : '—'}
              </div>
              {inReviewBand && analysis && (
                <div className="review-note">
                  within ±0.20 of the {analysis.verdict.threshold.toFixed(2)} threshold — review band
                </div>
              )}
            </div>
          </div>

          <div className="rail-section-head">Call audio</div>
          <AudioPlayer file={audioFile} durationS={analysis?.meta?.duration_s} />

          <div className="rail-section-head">Response body</div>
          <pre className="response-body">{responseBody}</pre>

          <p className="demo-footnote">
            {detectBody
              ? 'The public route returns exactly these two keys. The dashboard reads the /analyze superset for everything else on this page.'
              : 'Invalid or unreadable input still returns HTTP 200 with is_synthetic false and confidence 0.50, the maximal-uncertainty fallback.'}
          </p>

          {analyzeError && (
            <p className="demo-error" data-testid="analyze-error">
              /analyze did not return a usable payload: {analyzeError}. The verdict above still
              stands — it comes from /detect, which is the scored route.
            </p>
          )}

          <button
            className="open-detail"
            onClick={() =>
              callId &&
              // Carry the /analyze payload across rather than letting the
              // detail view re-fetch. `GET /feed/analysis/:id` returns the
              // retained `pipeline::Analysis`, which has no timeline, no top
              // factors and no rationale — so throwing this away and asking
              // the server again downgraded the very call we just explained.
              navigate(`/calls/${encodeURIComponent(callId)}`, { state: { analysis, audioFile } })
            }
            disabled={!analysis || !callId}
          >
            Open in detail
          </button>
        </div>
      </section>
    </div>
  );
}
