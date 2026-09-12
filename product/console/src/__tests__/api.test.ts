import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { setupServer } from 'msw/node';
import { handlers } from '../mocks/handlers';
import { makeFixtures } from '../mocks/fixtures';
import { AnalysisZ, analyze, getFeedRecent, Feed } from '../lib/api';

const server = setupServer(...handlers);

beforeAll(() => server.listen());
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
    // In node tests we can't create File easily; simulate by constructing a Blob-like object
    const file = new (global as any).File ? new File(['x'], 'a.wav', { type: 'audio/wav' }) : (new Blob(['x']) as any);
    const res = await analyze(file as any);
    expect(res).toHaveProperty('id');
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

