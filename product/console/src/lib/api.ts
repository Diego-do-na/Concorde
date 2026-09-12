export const API_BASE = (typeof import.meta !== 'undefined' && (import.meta as any).env && (import.meta as any).env.VITE_API_BASE) || '';
export const USE_MOCKS = (typeof import.meta !== 'undefined' && (import.meta as any).env && (import.meta as any).env.VITE_USE_MOCKS) === '1';

// TypeScript types mirroring spec §8.2 (plus waveform enrichment)
export type Verdict = { is_synthetic: boolean; confidence: number; threshold: number };
export type Signals = {
  behavioral: Record<string, any>;
  semantic: Record<string, any> | null;
  acoustic: Record<string, any> | null;
};
export type Degraded = { semantic_available: boolean; acoustic_available: boolean };
export type TimelinePoint = { t: number; confidence: number };
export type Turns = { caller: [number, number][]; agent: [number, number][] };
export type Event = { type: 'overlap' | 'interruption' | 'silence'; t: number; duration: number };
export type TopFactor = { feature: string; value: number; direction: 'synthetic' | 'human'; weight: number };
export type Timings = { decode: number; vad: number; features: number; semantic: number | null; inference: number; total: number };
export type Meta = { model_version: string; git_sha: string; feature_contract: string; duration_s: number };
export type Waveform = { caller: number[]; agent: number[]; bucket_ms: number };

export type Analysis = {
  id: string;
  verdict: Verdict;
  signals: Signals;
  degraded: Degraded;
  timeline: TimelinePoint[];
  turns: Turns;
  events: Event[];
  features: Record<string, number>;
  top_factors: TopFactor[];
  rationale: string;
  timings_ms: Timings & { semantic: number | null };
  meta: Meta;
  waveform?: Waveform; // console-only enrichment
};

// zod schemas for runtime validation (used in tests)
import { z } from 'zod';
export const VerdictZ = z.object({ is_synthetic: z.boolean(), confidence: z.number(), threshold: z.number() });
export const SignalsZ = z.object({
  behavioral: z.record(z.any()),
  semantic: z.nullable(z.record(z.any())),
  acoustic: z.nullable(z.record(z.any())),
});
export const DegradedZ = z.object({ semantic_available: z.boolean(), acoustic_available: z.boolean() });
export const TimelineZ = z.array(z.object({ t: z.number(), confidence: z.number() }));
export const TurnsZ = z.object({
  caller: z.array(z.tuple([z.number(), z.number()])),
  agent: z.array(z.tuple([z.number(), z.number()])),
});
export const EventZ = z.array(
  z.object({ type: z.union([z.literal('overlap'), z.literal('interruption'), z.literal('silence')]), t: z.number(), duration: z.number() })
);
export const TopFactorZ = z.array(
  z.object({ feature: z.string(), value: z.number(), direction: z.union([z.literal('synthetic'), z.literal('human')]), weight: z.number() })
);
export const TimingsZ = z.object({
  decode: z.number(),
  vad: z.number(),
  features: z.number(),
  semantic: z.nullable(z.number()),
  inference: z.number(),
  total: z.number(),
});
export const MetaZ = z.object({ model_version: z.string(), git_sha: z.string(), feature_contract: z.string(), duration_s: z.number() });
export const WaveformZ = z.object({ caller: z.array(z.number()), agent: z.array(z.number()), bucket_ms: z.number() });
export const AnalysisZ = z.object({
  id: z.string(),
  verdict: VerdictZ,
  signals: SignalsZ,
  degraded: DegradedZ,
  timeline: TimelineZ,
  turns: TurnsZ,
  events: EventZ,
  features: z.record(z.number()),
  top_factors: TopFactorZ,
  rationale: z.string(),
  timings_ms: TimingsZ,
  meta: MetaZ,
  waveform: WaveformZ.optional(),
});

// HTTP helpers
async function okJson(r: Response) {
  const text = await r.text();
  try {
    return text ? JSON.parse(text) : null;
  } catch {
    return null;
  }
}

export async function getHealth() {
  const res = await fetch(`${API_BASE}/health`);
  return okJson(res);
}

export async function getMetrics() {
  const res = await fetch(`${API_BASE}/metrics`);
  return okJson(res);
}

export async function getFeedRecent() {
  const res = await fetch(`${API_BASE}/feed/recent`);
  if (res.status === 200) return okJson(res);
  return null;
}

export async function getFeedAnalysis(id: string): Promise<Analysis | null> {
  const res = await fetch(`${API_BASE}/feed/analysis/${encodeURIComponent(id)}`);
  if (res.status === 404) return null;
  const body = await okJson(res);
  return body ? AnalysisZ.parse(body) : null;
}

type AnalyzeInput = { audio_base64: string } | File;

export async function analyze(input: AnalyzeInput): Promise<Analysis> {
  if (input instanceof File) {
    const form = new FormData();
    form.append('file', input);
    const res = await fetch(`${API_BASE}/analyze`, { method: 'POST', body: form });
    const body = await okJson(res);
    return AnalysisZ.parse(body);
  } else {
    const res = await fetch(`${API_BASE}/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audio_base64: input.audio_base64 }),
    });
    const body = await okJson(res);
    return AnalysisZ.parse(body);
  }
}

// Simple Feed class: opens WS at /ws and falls back to polling GET /feed/recent every 3s when socket closed/errors.
type FeedListener = (items: Analysis[]) => void;

export class Feed {
  private ws: WebSocket | null = null;
  private pollingId: number | null = null;
  private listeners: FeedListener[] = [];
  public online = false;
  public lastItems: Analysis[] = [];

  constructor(private base = API_BASE) {
    this.openSocket();
  }

  private openSocket() {
    try {
      const proto = typeof (globalThis as any).location !== 'undefined' && (globalThis as any).location.protocol === 'https:' ? 'wss' : 'ws';
      const host = typeof (globalThis as any).location !== 'undefined' ? (globalThis as any).location.host : 'localhost';
      const url = `${proto}://${host}${this.base}/ws`;
      this.ws = new WebSocket(url);
      this.ws.onopen = () => {
        this.online = true;
        this.stopPolling();
      };
      this.ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data);
          const parsed = Array.isArray(data) ? data.map((d) => AnalysisZ.parse(d)) : [AnalysisZ.parse(data)];
          this.lastItems = parsed;
          this.emit(parsed);
        } catch {
          // ignore invalid messages
        }
      };
      this.ws.onclose = () => {
        this.online = false;
        this.startPolling();
      };
      this.ws.onerror = () => {
        this.online = false;
        this.startPolling();
      };
    } catch {
      this.startPolling();
    }
  }

  private startPolling() {
    if (this.pollingId) return;
      this.pollingId = (globalThis as any).setInterval(async () => {
      try {
        const items = await getFeedRecent();
        if (items) {
          const parsed = Array.isArray(items) ? items.map((d) => AnalysisZ.parse(d)) : [];
          this.lastItems = parsed;
          this.emit(parsed);
        }
      } catch {
        // silent
      }
    }, 3000) as any;
  }

  private stopPolling() {
    if (this.pollingId) {
      (globalThis as any).clearInterval(this.pollingId);
      this.pollingId = null;
    }
  }

  private emit(items: Analysis[]) {
    for (const l of this.listeners) l(items);
  }

  subscribe(cb: FeedListener) {
    this.listeners.push(cb);
    if (this.lastItems.length) cb(this.lastItems);
    return () => {
      this.listeners = this.listeners.filter((x) => x !== cb);
    };
  }

  close() {
    try {
      this.ws?.close();
    } catch {}
    this.stopPolling();
  }
}

