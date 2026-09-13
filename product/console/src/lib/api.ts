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
  if (!body) return null;
  const direct = AnalysisZ.safeParse(body);
  if (direct.success) return direct.data;
  return adaptRetained(id, body);
}

// /detect endpoint returns only the verdict object (two-key JSON).
export async function detect(input: { audio_base64?: string } | File): Promise<{ is_synthetic: boolean; confidence: number }> {
  if (input instanceof File) {
    const form = new FormData();
    form.append('file', input);
    const res = await fetch(`${API_BASE}/detect`, { method: 'POST', body: form });
    const body = await okJson(res);
    if (!body || typeof body.is_synthetic !== 'boolean' || typeof body.confidence !== 'number') {
      return { is_synthetic: false, confidence: 0.5 };
    }
    return { is_synthetic: body.is_synthetic, confidence: body.confidence };
  } else {
    const res = await fetch(`${API_BASE}/detect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    });
    const body = await okJson(res);
    if (!body || typeof body.is_synthetic !== 'boolean' || typeof body.confidence !== 'number') {
      return { is_synthetic: false, confidence: 0.5 };
    }
    return { is_synthetic: body.is_synthetic, confidence: body.confidence };
  }
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

// ---------------------------------------------------------------------------
// Feed: what the backend actually publishes (product/api/src/feed.rs FeedEvent)
// ---------------------------------------------------------------------------
// /feed/recent and the /ws stream carry FeedEvent objects, NOT the /analyze
// superset. The first console build validated them against AnalysisZ, so
// every real event failed validation and the monitor stayed on "No calls
// processed yet" (found in the pre-freeze audit, 2026-09-13). Events are now
// parsed with FeedEventZ and adapted to the Analysis shape the views render;
// fixtures/mocks that already send Analysis-shaped rows keep working.
export const FeedEventZ = z.object({
  id: z.string(),
  ts: z.number(),
  duration_s: z.number(),
  is_synthetic: z.boolean(),
  confidence: z.number(),
  latency_ms: z.number(),
  signals: z.object({ behavioral: z.boolean(), semantic: z.nullable(z.boolean()), acoustic: z.nullable(z.boolean()) }),
  model_version: z.nullable(z.string()).optional(),
});
export type FeedEvent = z.infer<typeof FeedEventZ>;

// Shipped decision threshold (product/artifacts/model.onnx.meta.json). The
// feed and the retained analysis do not carry it; only POST /analyze does.
export const SHIPPED_THRESHOLD = 0.4198;

export function feedEventToAnalysis(e: FeedEvent): Analysis & { ts?: number } {
  return {
    id: e.id,
    ts: e.ts,
    verdict: { is_synthetic: e.is_synthetic, confidence: e.confidence, threshold: SHIPPED_THRESHOLD },
    signals: { behavioral: { available: e.signals.behavioral }, semantic: e.signals.semantic ? { available: true } : null, acoustic: e.signals.acoustic ? { available: true } : null },
    degraded: { semantic_available: !!e.signals.semantic, acoustic_available: !!e.signals.acoustic },
    timeline: [],
    turns: { caller: [], agent: [] },
    events: [],
    features: {},
    top_factors: [],
    rationale: '',
    timings_ms: { decode: 0, vad: 0, features: 0, semantic: null, inference: 0, total: e.latency_ms },
    meta: { model_version: e.model_version ?? '', git_sha: '', feature_contract: 'fc-1', duration_s: e.duration_s },
  };
}

function dedupeById(rows: Analysis[]): Analysis[] {
  const seen = new Set<string>();
  return rows.filter((r) => (seen.has(r.id) ? false : (seen.add(r.id), true)));
}

function parseFeedRows(data: unknown): Analysis[] {
  const arr = Array.isArray(data) ? data : [data];
  const out: Analysis[] = [];
  for (const d of arr) {
    if (d && typeof d === 'object' && 'verdict' in (d as any)) {
      const r = AnalysisZ.safeParse(d);
      if (r.success) out.push(r.data);
      continue;
    }
    const r = FeedEventZ.safeParse(d);
    if (r.success) out.push(feedEventToAnalysis(r.data));
  }
  return out;
}

// GET /feed/analysis/:id returns the backend's retained pipeline::Analysis
// (turns with channel, events with kind/start/end, features as pairs), not the
// /analyze superset. Adapt it; fields only /analyze computes (timeline, top
// factors, rationale) are left empty and labelled as such.
export function adaptRetained(id: string, raw: any): Analysis {
  const turns = { caller: [] as [number, number][], agent: [] as [number, number][] };
  for (const t of raw.turns ?? []) {
    (t.channel === 0 ? turns.caller : turns.agent).push([t.start, t.end]);
  }
  const events: Event[] = (raw.events ?? []).map((e: any) => ({
    type: (e.kind ?? e.type) as Event['type'],
    t: e.start ?? e.t,
    duration: e.duration ?? Math.max(0, (e.end ?? 0) - (e.start ?? 0)),
  }));
  const features: Record<string, number> = Array.isArray(raw.features)
    ? Object.fromEntries(raw.features)
    : (raw.features ?? {});
  const sem = raw.semantic ?? {};
  return {
    id,
    verdict: { is_synthetic: raw.verdict.is_synthetic, confidence: raw.verdict.confidence, threshold: SHIPPED_THRESHOLD },
    signals: {
      behavioral: { p_synthetic: raw.verdict.p_synthetic },
      semantic: sem.available ? { invention_score: sem.invention_score, answer_type: sem.answer_type } : null,
      acoustic: null,
    },
    degraded: { semantic_available: !!sem.available, acoustic_available: false },
    timeline: [],
    turns,
    events,
    features,
    top_factors: [],
    rationale: 'Retained analysis: top factors, rationale and the confidence trace are computed only by POST /analyze and are not stored in the feed.',
    timings_ms: { ...raw.timings_ms, semantic: sem.asr_ms ?? null },
    meta: { model_version: raw.model_version ?? '', git_sha: '', feature_contract: 'fc-1', duration_s: raw.duration_s },
    waveform: raw.waveform,
  };
}

// Feed: opens WS at /ws, backfills from GET /feed/recent immediately, and falls
// back to polling /feed/recent every 3s when the socket is closed/errors.
type FeedListener = (items: Analysis[]) => void;
export class Feed {
  private ws: WebSocket | null = null;
  private pollingId: number | null = null;
  private listeners: FeedListener[] = [];
  public online = false;
  public lastItems: Analysis[] = [];
  constructor(private base = API_BASE) {
    this.backfill();
    this.openSocket();
  }
  private async backfill() {
    try {
      const items = await getFeedRecent();
      if (items) {
        const parsed = dedupeById(parseFeedRows(items));
        if (parsed.length) {
          // WS frames may already have arrived: keep them, newest first
          const merged = dedupeById([...this.lastItems, ...parsed]).slice(0, 500);
          this.lastItems = merged;
          this.emit(merged);
        }
      }
    } catch {
      // silent: polling/WS will retry
    }
  }
  private openSocket() {
    try {
      if (typeof (globalThis as any).WebSocket === 'undefined') {
        this.startPolling();
        return;
      }
      const proto = typeof (globalThis as any).location !== 'undefined' && (globalThis as any).location.protocol === 'https:' ? 'wss' : 'ws'
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
          const parsed = parseFeedRows(data);
          if (!parsed.length) return;
          // a WS frame carries one new event: prepend, newest first, dedupe by id
          const merged = dedupeById([...parsed, ...this.lastItems]).slice(0, 500);
          this.lastItems = merged;
          this.emit(merged);
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
          const parsed = dedupeById(parseFeedRows(items));
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

