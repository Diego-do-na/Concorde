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
  it('marks p95 latency over 3000ms as over', () => {
    render(<ExecView />);
    const p95 = (metrics as any).latency_ms.p95;
    const el = screen.getByText(new RegExp(`${p95} ms`));
    expect(el.className).toMatch(/latency/);
    if (p95 > 3000) {
      expect(el.className).toMatch(/over/);
    }
  });
});

