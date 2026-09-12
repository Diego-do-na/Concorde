// Deterministic fixture generator ported from reference buildCall()
// xorshift32 RNG
function xorshift(seed: number) {
  let x = seed >>> 0;
  return () => {
    x ^= x << 13;
    x ^= x >>> 17;
    x ^= x << 5;
    return (x >>> 0) / 0xffffffff;
  };
}

function randInt(rng: () => number, a: number, b: number) {
  return Math.floor(rng() * (b - a + 1)) + a;
}

export function makeWaveform(rng: () => number, duration_s = 10) {
  const bucket_ms = 50;
  const buckets = Math.ceil((duration_s * 1000) / bucket_ms);
  const caller: number[] = [];
  const agent: number[] = [];
  for (let i = 0; i < buckets; i++) {
    caller.push(Math.floor(rng() * 32767));
    agent.push(Math.floor(rng() * 32767));
  }
  return { caller, agent, bucket_ms };
}

export function buildCall(seed: number, id: number) {
  const rng = xorshift(seed + id);
  const duration_s = randInt(rng, 6, 20);
  const verdict = { is_synthetic: rng() > 0.7, confidence: Number((rng() * 0.4 + 0.6).toFixed(3)), threshold: 0.5 };
  const timeline = Array.from({ length: randInt(rng, 3, 8) }).map((_, i) => ({ t: i * (duration_s / 8), confidence: Number((rng() * 0.5 + 0.5).toFixed(3)) }));
  const turns = { caller: [[0, 1]], agent: [[1, 2]] };
  const events = [{ type: 'silence', t: 0, duration: 0 }];
  const top_factors = [{ feature: 'F-01', value: Number(rng().toFixed(3)), direction: verdict.is_synthetic ? 'synthetic' : 'human', weight: Number((rng() * 2).toFixed(3)) }];
  const analysis = {
    id: `call-${id}`,
    verdict,
    signals: { behavioral: {}, semantic: null, acoustic: null },
    degraded: { semantic_available: false, acoustic_available: false },
    timeline,
    turns,
    events,
    features: { 'F-01': 0.1 },
    top_factors,
    rationale: 'Generated',
    timings_ms: { decode: 10, vad: 20, features: 30, semantic: null, inference: 40, total: 100 },
    meta: { model_version: 'dev', git_sha: 'local', feature_contract: 'fc-1', duration_s },
    waveform: makeWaveform(rng, duration_s),
  };
  return analysis;
}

export function makeFixtures(seed = 0x12345678) {
  const out = [];
  for (let i = 0; i < 14; i++) out.push(buildCall(seed, i));
  return out;
}

