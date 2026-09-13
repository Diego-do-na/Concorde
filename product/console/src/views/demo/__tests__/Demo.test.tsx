import React from 'react';
import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Demo from '../Demo';
import { setupServer } from 'msw/node';
import { handlers } from '../../../mocks/handlers';
import { MemoryRouter } from 'react-router-dom';

const server = setupServer(...handlers);
let originalFetch: any;
beforeAll(() => {
  server.listen();
  originalFetch = (global as any).fetch;
});
afterAll(() => {
  server.close();
  (global as any).fetch = originalFetch;
});

describe('Demo view', () => {
  it('uploads prewired clip, calls /detect and /analyze and renders detect body', async () => {
    // mock fetch for demo clip file to return a small blob; delegate other requests to real fetch so msw can handle them
    const originalFetch = (global as any).fetch;
    (global as any).fetch = (url: string, opts?: any) => {
      if (typeof url === 'string' && url.includes('/demo/')) {
        return Promise.resolve(new Response(new Blob(['x'], { type: 'audio/wav' })));
      }
      return originalFetch(url, opts);
    };

    render(
      <MemoryRouter>
        <Demo />
      </MemoryRouter>
    );

    const btn = await screen.findByRole('button', { name: /Human caller/ });
    await userEvent.click(btn);

    // detect response body should appear
    const pre = await screen.findByText(/is_synthetic/);
    expect(pre).toBeTruthy();

    // Open in detail should be disabled until analyze resolves; when it does, button becomes enabled
    const openBtn = screen.getByRole('button', { name: /Open in detail/ });
    // wait for it to become enabled (handlers produce HTTP 200 analyze)
    await screen.findByRole('button', { name: /Open in detail/ });
    expect(openBtn).toBeEnabled();
  });
});

