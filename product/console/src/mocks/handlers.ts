import { rest } from 'msw';
import { makeFixtures } from './fixtures';

const fixtures = makeFixtures();

export const handlers = [
  rest.get('/feed/recent', (req, res, ctx) => {
    return res(ctx.status(200), ctx.json(fixtures));
  }),
  rest.get('/feed/analysis/:id', (req, res, ctx) => {
    const { id } = req.params as any;
    const found = fixtures.find((f) => f.id === id);
    if (!found) return res(ctx.status(404));
    return res(ctx.status(200), ctx.json(found));
  }),
  rest.get('/health', (req, res, ctx) => res(ctx.status(200), ctx.json({ ok: true }))),
  rest.get('/metrics', (req, res, ctx) => res(ctx.status(200), ctx.text('metrics: 1'))),
  rest.post('/analyze', async (req, res, ctx) => {
    // Accept multipart or json
    const contentType = req.headers.get('content-type') || '';
    let payload: any = null;
    if (contentType.includes('application/json')) {
      const body = await req.json();
      payload = body;
    } else {
      // msw exposes body as FormData for multipart
      try {
        const form = await req.formData();
        const file = form.get('file');
        payload = { file: !!file };
      } catch {
        payload = {};
      }
    }
    // respond with a deterministic analysis
    const id = `an-${Math.floor(Math.random() * 10000)}`;
    const sample = fixtures[0];
    const resp = { ...sample, id, waveform: sample.waveform };
    return res(ctx.status(200), ctx.json(resp));
  }),
];

