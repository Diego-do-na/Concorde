import React, { useEffect, useMemo, useRef, useState } from 'react';
import { mmss } from '../../../theme/tokens';
import './audio.css';

/*
 * Call audio playback for human debugging: an operator (or a judge) can hear
 * the exact instant an interruption, a silence or the confidence peak
 * happened, not just see it plotted.
 *
 * Audio is only ever available for clips analysed in THIS browser session
 * (the Demo route keeps the uploaded File in memory and hands it to the
 * detail view). The service itself never retains audio — NFR-011 / ADR-008:
 * `/detect` and `/analyze` decode the WAV, score it and drop the bytes —
 * so a call opened from the monitor, the recent-calls rail or history has
 * no audio to play. That is a first-class state here ("not available"),
 * never a silent failure and never something that breaks the rest of the
 * view.
 *
 * `onTime` is the sync hook: while playing, the current position is pushed
 * up so the confidence trace's playhead follows the audio.
 */

export type AudioPlayerProps = {
  /** The clip to play; `null` = no audio for this call. */
  file: File | Blob | null;
  /** Fallback duration (from the analysis) shown before metadata loads. */
  durationS?: number;
  /** Fired with the current position while playing/seeking; `null` on reset. */
  onTime?: (t: number | null) => void;
  /** Why audio is missing, when it is. Defaults to the retention statement. */
  unavailableReason?: string;
};

type State = 'unavailable' | 'loading' | 'ready' | 'playing' | 'error';

const DEFAULT_REASON =
  'Audio not available — the service does not retain call audio (NFR-011); only clips analysed in this browser session can be replayed.';

export default function AudioPlayer({ file, durationS, onTime, unavailableReason }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [state, setState] = useState<State>(file ? 'loading' : 'unavailable');
  const [t, setT] = useState(0);
  const [dur, setDur] = useState<number>(durationS ?? 0);

  // One object URL per file, revoked when the file changes or we unmount —
  // otherwise every demo run leaks a blob URL for the session's lifetime.
  // Guarded: a runtime without object URLs (jsdom, some embedded webviews)
  // must land in the explicit error state, not throw during render.
  const canObjectUrl = typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function';
  const url = useMemo(() => (file && canObjectUrl ? URL.createObjectURL(file) : null), [file, canObjectUrl]);
  useEffect(() => {
    return () => {
      if (url && typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(url);
    };
  }, [url]);

  useEffect(() => {
    setState(file ? (url ? 'loading' : 'error') : 'unavailable');
    setT(0);
    setDur(durationS ?? 0);
    onTime?.(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file, url]);

  const push = (value: number) => {
    setT(value);
    onTime?.(value);
  };

  const toggle = async () => {
    const el = audioRef.current;
    if (!el || state === 'unavailable' || state === 'error' || state === 'loading') return;
    try {
      if (el.paused) {
        await el.play();
      } else {
        el.pause();
      }
    } catch {
      // Autoplay policy or an undecodable container: surface it, never throw
      // into React's render path.
      setState('error');
    }
  };

  const seek = (value: number) => {
    const el = audioRef.current;
    if (el && Number.isFinite(value)) {
      el.currentTime = value;
    }
    push(value);
  };

  const unavailable = state === 'unavailable' || state === 'error';
  const total = dur > 0 ? dur : durationS ?? 0;

  return (
    <div className={`audio-player is-${state}`} data-testid="audio-player" data-state={state}>
      {url && (
        <audio
          ref={audioRef}
          src={url}
          preload="metadata"
          onLoadedMetadata={(e) => {
            const d = e.currentTarget.duration;
            if (Number.isFinite(d) && d > 0) setDur(d);
            setState('ready');
          }}
          onPlay={() => setState('playing')}
          onPause={() => setState((s) => (s === 'playing' ? 'ready' : s))}
          onEnded={() => setState('ready')}
          onTimeUpdate={(e) => push(e.currentTarget.currentTime)}
          onError={() => setState('error')}
        />
      )}

      <button
        type="button"
        className="audio-toggle"
        onClick={toggle}
        disabled={unavailable || state === 'loading'}
        aria-label={state === 'playing' ? 'Pause' : 'Play'}
        data-testid="audio-toggle"
      >
        {state === 'playing' ? '❚❚' : '▶'}
      </button>

      <div className="audio-body">
        {unavailable ? (
          <div className="audio-unavailable" data-testid="audio-unavailable" role="status">
            {state === 'error'
              ? 'Audio could not be decoded by this browser — the analysis above still stands.'
              : unavailableReason ?? DEFAULT_REASON}
          </div>
        ) : (
          <>
            <input
              type="range"
              className="audio-timeline"
              min={0}
              max={total || 0}
              step={0.05}
              value={Math.min(t, total || 0)}
              onChange={(e) => seek(parseFloat(e.target.value))}
              aria-label="Playback position"
              data-testid="audio-timeline"
              disabled={state === 'loading'}
            />
            <div className="audio-time">
              <span data-testid="audio-current">{mmss(t)}</span>
              <span className="audio-sep">/</span>
              <span>{mmss(total)}</span>
              <span className="audio-hint">
                {state === 'loading' ? 'loading…' : 'playhead is synced with the confidence trace'}
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
