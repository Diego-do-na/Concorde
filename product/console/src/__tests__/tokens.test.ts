import { describe, it, expect } from 'vitest'
import { tokens } from '../theme/tokens'

describe('tokens and helpers', () => {
  it('verdict mapping', () => {
    expect(tokens.verdictOf(true, 0.7)).toBe('synthetic')
    expect(tokens.verdictOf(false, 0.71)).toBe('verified')
    expect(tokens.verdictOf(true, 0.69)).toBe('review')
  })

  it('semantic token strings present verbatim', () => {
    // VERIFIED
    expect(tokens.semantic.VERIFIED.color).toBe('oklch(0.74 0.12 168)')
    expect(tokens.semantic.VERIFIED.dim).toBe('oklch(0.42 0.07 168)')
    expect(tokens.semantic.VERIFIED.wash).toBe('oklch(0.27 0.035 168)')

    // REVIEW
    expect(tokens.semantic.REVIEW.color).toBe('oklch(0.79 0.13 78)')
    expect(tokens.semantic.REVIEW.dim).toBe('oklch(0.44 0.08 78)')
    expect(tokens.semantic.REVIEW.wash).toBe('oklch(0.28 0.04 78)')

    // SYNTHETIC
    expect(tokens.semantic.SYNTHETIC.color).toBe('oklch(0.66 0.17 24)')
    expect(tokens.semantic.SYNTHETIC.dim).toBe('oklch(0.40 0.09 24)')
    expect(tokens.semantic.SYNTHETIC.wash).toBe('oklch(0.27 0.045 24)')
  })
})

import { describe, it, expect } from 'vitest'
import { verdictOf, SEMANTIC, DEGRADED } from '../theme/tokens'

describe('verdicts and tokens', () => {
  it('verdict mapping works', () => {
    expect(verdictOf(true, 0.7)).toBe('synthetic')
    expect(verdictOf(false, 0.71)).toBe('verified')
    expect(verdictOf(true, 0.69)).toBe('review')
  })

  it('semantic strings are present verbatim', () => {
    // check literals required by spec are present
    expect(SEMANTIC.VERIFIED.COLOR).toBe('oklch(0.74 0.12 168)')
    expect(SEMANTIC.VERIFIED.DIM).toBe('oklch(0.42 0.07 168)')
    expect(SEMANTIC.VERIFIED.WASH).toBe('oklch(0.27 0.035 168)')

    expect(SEMANTIC.REVIEW.COLOR).toBe('oklch(0.79 0.13 78)')
    expect(SEMANTIC.REVIEW.DIM).toBe('oklch(0.44 0.08 78)')
    expect(SEMANTIC.REVIEW.WASH).toBe('oklch(0.28 0.04 78)')

    expect(SEMANTIC.SYNTHETIC.COLOR).toBe('oklch(0.66 0.17 24)')
    expect(SEMANTIC.SYNTHETIC.DIM).toBe('oklch(0.40 0.09 24)')
    expect(SEMANTIC.SYNTHETIC.WASH).toBe('oklch(0.27 0.045 24)')

    expect(DEGRADED.HATCH).toBe('repeating-linear-gradient(135deg, oklch(0.46 0.012 252) 0 3px, oklch(0.3 0.014 252) 3px 6px)')
    expect(DEGRADED.LABEL).toBe('DEGRADED')
  })
})

