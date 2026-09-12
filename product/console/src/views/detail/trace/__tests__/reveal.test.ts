import { describe, it, expect, afterEach, vi } from 'vitest'
import { prefersReducedMotion, initialProgress } from '../reveal'

describe('initialProgress', () => {
  it('reduced motion disables the draw-in animation (starts already fully revealed)', () => {
    expect(initialProgress(true)).toBe(1)
  })

  it('otherwise starts the 300ms linear reveal from 0', () => {
    expect(initialProgress(false)).toBe(0)
  })
})

describe('prefersReducedMotion', () => {
  // No jsdom in this project (see waveform/__tests__ for the established
  // pattern: pure logic only, no DOM environment) — stub `window` as a
  // plain global rather than depending on one being present.
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('reflects a truthy (prefers-reduced-motion: reduce) media query', () => {
    vi.stubGlobal('window', { matchMedia: vi.fn().mockReturnValue({ matches: true }) })
    expect(prefersReducedMotion()).toBe(true)
  })

  it('reflects a falsy media query', () => {
    vi.stubGlobal('window', { matchMedia: vi.fn().mockReturnValue({ matches: false }) })
    expect(prefersReducedMotion()).toBe(false)
  })

  it('defaults to false when matchMedia is unavailable', () => {
    vi.stubGlobal('window', {})
    expect(prefersReducedMotion()).toBe(false)
  })

  it('defaults to false when window itself is unavailable', () => {
    vi.stubGlobal('window', undefined)
    expect(prefersReducedMotion()).toBe(false)
  })
})
