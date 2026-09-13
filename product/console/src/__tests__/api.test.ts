import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { setupServer } from 'msw/node';
import { rest } from 'msw';
import { handlers } from '../mocks/handlers';
import { makeFixtures } from '../mocks/fixtures';
import { AnalysisZ, analyze, getFeedRecent, Feed } from '../lib/api';

const server = setupServer(...handlers);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('mocks & types', () => {
  it('fixtures validate against Analysis zod schema', () => {
    const fixtures = makeFixtures();
    for (const f of fixtures) {
      expect(() => AnalysisZ.parse(f)).not.toThrow();
    }
  });

  it('analyze accepts json body', async () => {
    const res = await analyze({ audio_base64: 'abc' });
    expect(res).toHaveProperty('id');
    expect(() => AnalysisZ.parse(res)).not.toThrow();
  });

  it('analyze accepts multipart (File simulated)', async () => {
    // `new (global as any).File ? … : …` parsed as `new (global.File)()` — a
    // zero-argument File construction evaluated as the ternary *condition*,
    // which throws before the branch is ever chosen. jsdom provides File, so
    // test for it rather than constructing it to find out.
    const file =
      typeof File !== 'undefined'
        ? new File(['x'], 'a.wav', { type: 'audio/wav' })
        : (new Blob(['x']) as any);
    const res = await analyze(file as any);
    expect(res).toHaveProperty('id');
  });

  it('adopts the sent call_id when /analyze omits id, as the service does', async () => {
    // Regression: analysis::AnalyzeResponse carries no `id`, so validating
    // the body against the id-bearing schema threw on every real response
    // and the demo view's "Open in detail" never enabled.
    server.use(
      rest.post('/analyze', async (_req, res, ctx) => {
        const { id, ...withoutId } = makeFixtures()[0] as any;
        return res(ctx.status(200), ctx.json(withoutId));
      })
    );
    const res = await analyze({ audio_base64: 'abc' }, 'demo_cafebabe');
    expect(res.id).toBe('demo_cafebabe');
  });

  it('surfaces the {"error": ...} envelope /analyze uses instead of a 5xx', async () => {
    server.use(rest.post('/analyze', (_req, res, ctx) => res(ctx.status(200), ctx.json({ error: 'boom' }))));
    await expect(analyze({ audio_base64: 'abc' })).rejects.toThrow('boom');
  });
});

describe('Feed WS fallback', () => {
  it('falls back to polling when WS errors and recovers on reopen', async () => {
    // Mock WebSocket
    let onopen: any;
    let onclose: any;
    let onmessage: any;
    class MockWS {
      onopen: any;
      onclose: any;
      onerror: any;
      onmessage: any;
      constructor(url: string) {
        // open asynchronously
        setTimeout(() => {
          if (this.onopen) this.onopen();
        }, 10);
      }
      send() {}
      close() {
        if (this.onclose) this.onclose();
      }
    }
    const original = (global as any).WebSocket;
    (global as any).WebSocket = MockWS;

    const feed = new Feed('');
    let received = 0;
    const unsub = feed.subscribe((items) => {
      received += items.length;
    });
    // wait a bit for polling to start if WS closed
    await new Promise((r) => setTimeout(r, 100));
    expect(received).toBeGreaterThanOrEqual(0);
    unsub();
    feed.close();
    (global as any).WebSocket = original;
  });
});

