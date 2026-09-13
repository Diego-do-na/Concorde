import { describe, it, expect } from 'vitest';
import { computeBusiness } from '../ExecView';
import metrics from '../metrics.json';
import React from 'react';
import { render, screen } from '@testing-library/react';
import ExecView from '../ExecView';

describe('ExecView business computations', () => {
  it('derives attempts and exposure correctly', () => {
    const inputs = {
      monthlyVolume: 150_000_000,
      shareScreened: 1.0,
      fraudRate: 0.0004,
      detectionRecall: (metrics as any).validation.detection_recall,
      lossPerIncident: 1850,
      inferenceCostPerCall: 0.00001
    };

    const derived = computeBusiness(inputs);
    const expectedAttempts = inputs.monthlyVolume * inputs.shareScreened * inputs.fraudRate;
    expect(derived.attemptsPerMonth).toBeCloseTo(expectedAttempts);
    expect(derived.attemptsCaught).toBeCloseTo(expectedAttempts * inputs.detectionRecall);
    expect(derived.exposureAvoided).toBeCloseTo(derived.attemptsCaught * inputs.lossPerIncident);
  });
});

describe('ExecView UI', () => {
  const m = metrics as any;

  it('marks p95 latency over 3000ms as over', () => {
    const { container } = render(<ExecView />);
    // p95 is now shown twice — in the stats strip and in the percentile
    // panel — so scope the query instead of matching on text alone.
    const el = container.querySelector('.stat .value.latency') as HTMLElement;
    expect(el).not.toBeNull();
    expect(el.textContent).toContain(`${m.latency_ms.p95} ms`);
    expect(el.className.includes('over')).toBe(m.latency_ms.p95 > 3000);
  });

  it('renders every measured latency percentile against the 3 000 ms target', () => {
    render(<ExecView />);
    for (const [name, value] of Object.entries(m.latency_ms) as [string, number][]) {
      const row = screen.getByTestId(`latency-${name}`);
      expect(row).toHaveTextContent(`${value.toLocaleString()} ms`);
      const fill = row.querySelector('.bar-fill') as HTMLElement;
      expect(fill.className.includes('is-over')).toBe(value > 3000);
    }
  });

  it('shows each verdict band as a share of the feed window, not of itself', () => {
    render(<ExecView />);
    const v = m.verdicts;
    const total = v.verified + v.review + v.synthetic;
    for (const key of ['verified', 'review', 'synthetic'] as const) {
      const row = screen.getByTestId(`verdict-${key}`);
      expect(row).toHaveTextContent(`${((v[key] / total) * 100).toFixed(2)}%`);
      expect(row).toHaveTextContent(String(v[key]));
    }
  });

  it('prints the provenance statement that backs the MEASURED tags', () => {
    render(<ExecView />);
    expect(screen.getByText(new RegExp(m._source.slice(0, 40)))).toBeInTheDocument();
  });
});

