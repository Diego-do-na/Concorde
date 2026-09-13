import React from 'react';
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Demo from '../Demo';
import { setupServer } from 'msw/node';
import { rest } from 'msw';
import { handlers } from '../../../mocks/handlers';
import { MemoryRouter } from 'react-router-dom';

const server = setupServer(...handlers);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/** Serve the pre-wired clip; everything else goes to msw. */
function stubClipFetch() {
  const original = globalThis.fetch;
  globalThis.fetch = ((url: any, opts?: any) => {
    if (typeof url === 'string' && url.includes('/demo/')) {
      return Promise.resolve(new Response(new Blob(['x'], { type: 'audio/wav' })));
    }
    return original(url, opts);
  }) as typeof fetch;
  return () => {
    globalThis.fetch = original;
  };
}

describe('Demo view', () => {
  it('runs a clip through /detect and /analyze and shows the real response body', async () => {
    const restore = stubClipFetch();
    render(
      <MemoryRouter>
        <Demo />
      </MemoryRouter>
    );

    await userEvent.click(await screen.findByRole('button', { name: /Human caller/ }));

    // The placeholder also contains "is_synthetic", so assert on a value the
    // handler actually produced rather than on the key alone.
    await waitFor(() =>
      expect(screen.getByText(/"confidence":\s*[0-9]/)).toBeInTheDocument()
    );

    // "Open in detail" unlocks only once /analyze produced a usable payload.
    await waitFor(() => expect(screen.getByRole('button', { name: /Open in detail/ })).toBeEnabled());
    restore();
  });

  it('keeps the /detect verdict when /analyze fails, and says analyze failed', async () => {
    // /analyze is not scored: it reports failure as {"error": ...} at HTTP
    // 200 (ADR-013). The verdict on screen must survive that, because it
    // comes from /detect, which is the route that is graded.
    server.use(
      rest.post('/analyze', (_req, res, ctx) => res(ctx.status(200), ctx.json({ error: 'asr timeout' })))
    );
    const restore = stubClipFetch();

    render(
      <MemoryRouter>
        <Demo />
      </MemoryRouter>
    );

    await userEvent.click(await screen.findByRole('button', { name: /Synthetic caller/ }));

    await waitFor(() => expect(screen.getByTestId('analyze-error')).toHaveTextContent(/asr timeout/));
    expect(screen.getByText(/"confidence":\s*[0-9]/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Open in detail/ })).toBeDisabled();
    restore();
  });
});
