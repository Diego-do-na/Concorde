/*
 * Design tokens — §11 / NFR-012.
 *
 * ONE source of truth. This file previously carried two parallel token
 * systems (a lowercase `tokens` object and a set of UPPERCASE consts) that
 * were maintained by hand and had already drifted: components reached for
 * `tokens.semantic.SYNTHETIC.WASH` and `tokens.spacing.PANEL`, neither of
 * which existed on the lowercase object, so those styles silently resolved
 * to `undefined`. Both export shapes are still published — call sites and
 * tests depend on each — but they are now *derived* from the primitives
 * below, and the semantic/spacing leaves answer to either casing, so a
 * lookup can no longer miss.
 *
 * Colours are oklch on purpose: the three semantic states are specified at
 * matched lightness, which keeps VERIFIED/REVIEW/SYNTHETIC equally legible
 * against every surface instead of red reading darker than green.
 */

export type SemanticStateKey = 'VERIFIED' | 'REVIEW' | 'SYNTHETIC'
export type VerdictKey = 'verified' | 'review' | 'synthetic'

// ── primitives ────────────────────────────────────────────────────────────
// Surfaces are separated by luminance, never by shadow (spec: "elevated
// surfaces via luminance"). Base is bluish-charcoal, never pure black.
const SURFACE = {
  PAGE_BG: 'oklch(0.185 0.014 252)',
  PANEL: 'oklch(0.2 0.014 252)',
  PANEL_ALT: 'oklch(0.197 0.014 252)',
  HEADER: 'oklch(0.215 0.015 252)',
  PLOT: 'oklch(0.205 0.014 252)',
  CHIP_BG: 'oklch(0.235 0.015 252)',
  HOVER: 'oklch(0.245 0.016 252)',
  TOOLTIP_BG: 'oklch(0.28 0.016 252)',
  TOOLTIP_BORDER: 'oklch(0.4 0.016 252)',
  TABLE_ROW_ALT: 'oklch(0.225 0.015 252)',
  TABLE_ROW_ALT_2: 'oklch(0.207 0.014 252)'
} as const

const LINE = {
  LINE: 'oklch(0.3 0.016 252)',
  INNER_A: 'oklch(0.28 0.016 252)',
  INNER_B: 'oklch(0.25 0.015 252)',
  ROW_DIV: 'oklch(0.24 0.014 252)',
  CHIP_BORDER: 'oklch(0.32 0.016 252)'
} as const

const TEXT = {
  INK: 'oklch(0.94 0.006 252)',
  INK2: 'oklch(0.72 0.01 252)',
  MUTED: 'oklch(0.6 0.012 252)',
  DIM_LABEL: 'oklch(0.56 0.012 252)',
  MONO1: 'oklch(0.86 0.008 252)',
  MONO2: 'oklch(0.88 0.008 252)',
  MONO3: 'oklch(0.9 0.008 252)',
  PLAYHEAD: 'oklch(0.95 0.006 252)',
  LINK: 'oklch(0.78 0.05 252)',
  LINK_HOVER: 'oklch(0.9 0.05 252)',
  FOCUS: 'oklch(0.8 0.05 252)',
  SELECTION: 'oklch(0.4 0.05 252)'
} as const

// Exactly three semantic states (§11). Colour is never the only carrier of
// meaning — every consumer pairs these with the `label` and a shape/icon.
const STATE = {
  VERIFIED: { label: 'VERIFIED', color: 'oklch(0.74 0.12 168)', dim: 'oklch(0.42 0.07 168)', wash: 'oklch(0.27 0.035 168)' },
  REVIEW:   { label: 'REVIEW',   color: 'oklch(0.79 0.13 78)',  dim: 'oklch(0.44 0.08 78)',  wash: 'oklch(0.28 0.04 78)' },
  SYNTHETIC:{ label: 'SYNTHETIC',color: 'oklch(0.66 0.17 24)',  dim: 'oklch(0.40 0.09 24)',  wash: 'oklch(0.27 0.045 24)' }
} as const

/** Accepts `.color`/`.COLOR`, `.wash`/`.WASH`, `.dim`/`.DIM` alike. */
function dualCase<T extends { label: string; color: string; dim: string; wash: string }>(s: T) {
  return {
    ...s,
    LABEL: s.label,
    COLOR: s.color,
    DIM: s.dim,
    WASH: s.wash
  }
}

const SEMANTIC_DUAL = {
  VERIFIED: dualCase(STATE.VERIFIED),
  REVIEW: dualCase(STATE.REVIEW),
  SYNTHETIC: dualCase(STATE.SYNTHETIC)
} as const

const SPACE = {
  HEADER: '10px 20px',
  GAP: '20px',
  NAV_TAB: '9px 14px',
  SECTION_HEAD: '8px 12px',
  ROW: '7px 12px',
  TABLE_CELL: '6px 10px',
  PANEL: '14px 18px 22px',
  GRID_GAP: '0 12px',
  WAVEFORM_LANE: '66px',
  CONFIDENCE_TRACE: '84px',
  LANE_LABEL_COL: '84px'
} as const

// ── published shape A: UPPERCASE consts ───────────────────────────────────

export const FONTS = {
  BODY: '"IBM Plex Sans", system-ui, sans-serif',
  MONO: '"IBM Plex Mono", monospace',
  // Tabular + lining figures are mandatory: every numeric column in the
  // console (confidence, latency, duration) has to align vertically.
  FEATURE_SETTINGS: '"tnum" 1, "lnum" 1'
} as const

export const SURFACES = SURFACE
export const LINES = LINE
export const INK = TEXT
export const SEMANTIC = SEMANTIC_DUAL

export const DEGRADED = {
  // A hatch, not a colour: a degraded signal must be distinguishable from a
  // zero-valued one at a glance and without relying on hue (ADR-008).
  HATCH: 'repeating-linear-gradient(135deg, oklch(0.46 0.012 252) 0 3px, oklch(0.3 0.014 252) 3px 6px)',
  LABEL: 'DEGRADED'
} as const

export const TYPE = {
  LABEL_XS: '11px',
  LABEL_SM: '11px',
  BODY: '13px',
  METRIC: '19px',
  HERO: '30px',
  LABEL_TRACKING: '0.1em',
  WEIGHTS: { REG: 400, MED: 500, SEMI: 600 },
  LINE_HEIGHT: 1.45,
  BRAND_NAME: { SIZE: '13px', WEIGHT: 600, LETTER_SPACING: '0.14em' },
  BRAND_SUITE: { SIZE: '11px', WEIGHT: 400 }
} as const

export const RADII = {
  PANEL: '2px',
  BUTTON: '2px',
  BADGE: '2px',
  BAR: '1px',
  DOT: '50%'
} as const

export const SPACING = SPACE

export const MOTION = { REDUCED: '(prefers-reduced-motion: reduce)' } as const

export const BRAND = { NAME: 'CONCORDE', SUITE: 'CONSOLE' } as const

// ── helpers ───────────────────────────────────────────────────────────────

/**
 * Three-state mapping. A confident call in either direction is VERIFIED or
 * SYNTHETIC; anything inside the uncertainty band is REVIEW — the console
 * never shows a coin-flip as a decision.
 */
export function verdictOf(isSynthetic: boolean, confidence: number): VerdictKey {
  if (isSynthetic && confidence >= 0.7) return 'synthetic'
  if (!isSynthetic && confidence >= 0.7) return 'verified'
  return 'review'
}

export function toneFor(state: VerdictKey | SemanticStateKey) {
  const key = state.toUpperCase() as SemanticStateKey
  const s = SEMANTIC_DUAL[key] ?? SEMANTIC_DUAL.REVIEW
  return { label: s.label, color: s.color, dim: s.dim, wash: s.wash }
}

export function f2(n: number) {
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
export function pct(n: number) {
  return `${f2(n * 100)}%`
}
export function ms(n: number) {
  return `${Math.round(n).toLocaleString('en-US')} ms`
}
export function secs(n: number) {
  return `${f2(n)}s`
}
export function mmss(totalSeconds: number) {
  const m = Math.floor(totalSeconds / 60)
  const s = Math.floor(totalSeconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

// ── published shape B: the lowercase `tokens` object ──────────────────────
// Same primitives, grouped the way the detail-view components reach for
// them. Spacing and semantic leaves carry both casings (see dualCase).

export const tokens = {
  fonts: {
    body: FONTS.BODY,
    mono: FONTS.MONO,
    fontFeatureSettings: FONTS.FEATURE_SETTINGS
  },

  surfaces: {
    page: SURFACE.PAGE_BG,
    panel: SURFACE.PANEL,
    panelAlt: SURFACE.PANEL_ALT,
    header: SURFACE.HEADER,
    plot: SURFACE.PLOT,
    chipBg: SURFACE.CHIP_BG,
    hover: SURFACE.HOVER,
    tooltip: SURFACE.TOOLTIP_BG,
    tooltipBorder: SURFACE.TOOLTIP_BORDER,
    tableAlt1: SURFACE.TABLE_ROW_ALT,
    tableAlt2: SURFACE.TABLE_ROW_ALT_2
  },

  lines: {
    line: LINE.LINE,
    innerA: LINE.INNER_A,
    innerB: LINE.INNER_B,
    rowDivider: LINE.ROW_DIV,
    chipBorder: LINE.CHIP_BORDER
  },

  ink: TEXT,

  semantic: SEMANTIC_DUAL,

  degraded: { pattern: DEGRADED.HATCH, label: DEGRADED.LABEL },

  type: {
    label: TYPE.LABEL_XS,
    labelLetterSpacing: TYPE.LABEL_TRACKING,
    chipLabelLetterSpacing: { small: '0.06em', medium: '0.08em', large: '0.04em' },
    body: TYPE.BODY,
    bodyWeight: TYPE.WEIGHTS.MED,
    metricMono: TYPE.METRIC,
    hero: TYPE.HERO,
    brand: {
      concorde: { size: TYPE.BRAND_NAME.SIZE, weight: TYPE.BRAND_NAME.WEIGHT, letterSpacing: TYPE.BRAND_NAME.LETTER_SPACING },
      console: { size: TYPE.BRAND_SUITE.SIZE, weight: TYPE.BRAND_SUITE.WEIGHT }
    },
    lineHeight: TYPE.LINE_HEIGHT
  },

  radii: { panel: RADII.PANEL, bar: RADII.BAR, dot: RADII.DOT },

  // Both casings, for the same reason as the semantic leaves.
  spacing: {
    ...SPACE,
    headerPadding: SPACE.HEADER,
    headerGap: SPACE.GAP,
    navTab: SPACE.NAV_TAB,
    sectionHead: SPACE.SECTION_HEAD,
    listRow: SPACE.ROW,
    tableCell: SPACE.TABLE_CELL,
    panelPadding: SPACE.PANEL,
    gridGap: SPACE.GRID_GAP,
    waveformLane: SPACE.WAVEFORM_LANE,
    confidenceTrace: SPACE.CONFIDENCE_TRACE,
    laneLabelColumn: SPACE.LANE_LABEL_COL
  },

  motion: { functional: true, prefersReducedMotion: 'reduce' },

  verdictOf,
  toneFor,
  f2,
  pct,
  ms,
  secs,
  mmss
} as const

export type Tokens = typeof tokens

export default { FONTS, SURFACES, LINES, INK, SEMANTIC, DEGRADED, TYPE, RADII, SPACING, BRAND }
