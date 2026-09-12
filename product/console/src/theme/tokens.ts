export type SemanticStateKey = 'VERIFIED' | 'REVIEW' | 'SYNTHETIC'

export const tokens = {
  // Fonts
  fonts: {
    body: `IBM Plex Sans, system-ui, sans-serif`,
    mono: `IBM Plex Mono, monospace`,
    fontFeatureSettings: `"tnum" 1, "lnum" 1`
  },

  // Surfaces
  surfaces: {
    page: 'oklch(0.185 0.014 252)',
    panel: 'oklch(0.2 0.014 252)',
    panelAlt: 'oklch(0.197 0.014 252)',
    header: 'oklch(0.215 0.015 252)',
    plot: 'oklch(0.205 0.014 252)',
    chipBg: 'oklch(0.235 0.015 252)',
    hover: 'oklch(0.245 0.016 252)',
    tooltip: 'oklch(0.28 0.016 252)',
    tooltipBorder: 'oklch(0.4 0.016 252)',
    tableAlt1: 'oklch(0.225 0.015 252)',
    tableAlt2: 'oklch(0.207 0.014 252)'
  },

  // Lines
  lines: {
    line: 'oklch(0.3 0.016 252)',
    innerA: 'oklch(0.28 0.016 252)',
    innerB: 'oklch(0.25 0.015 252)',
    rowDivider: 'oklch(0.24 0.014 252)',
    chipBorder: 'oklch(0.32 0.016 252)'
  },

  // Ink / text
  ink: {
    INK: 'oklch(0.94 0.006 252)',
    INK2: 'oklch(0.72 0.01 252)',
    MUTED: 'oklch(0.6 0.012 252)',
    DIM_LABEL: 'oklch(0.56 0.012 252)',
    mono1: 'oklch(0.86 0.008 252)',
    mono2: 'oklch(0.88 0.008 252)',
    mono3: 'oklch(0.9 0.008 252)',
    playhead: 'oklch(0.95 0.006 252)',
    link: 'oklch(0.78 0.05 252)',
    linkHover: 'oklch(0.9 0.05 252)',
    focusOutline: 'oklch(0.8 0.05 252)',
    selection: 'oklch(0.4 0.05 252)'
  },

  // Semantic states (exactly three)
  semantic: {
    VERIFIED: {
      label: 'VERIFIED',
      color: 'oklch(0.74 0.12 168)',
      dim: 'oklch(0.42 0.07 168)',
      wash: 'oklch(0.27 0.035 168)'
    },
    REVIEW: {
      label: 'REVIEW',
      color: 'oklch(0.79 0.13 78)',
      dim: 'oklch(0.44 0.08 78)',
      wash: 'oklch(0.28 0.04 78)'
    },
    SYNTHETIC: {
      label: 'SYNTHETIC',
      color: 'oklch(0.66 0.17 24)',
      dim: 'oklch(0.40 0.09 24)',
      wash: 'oklch(0.27 0.045 24)'
    }
  },

  // Degraded
  degraded: {
    pattern:
      'repeating-linear-gradient(135deg, oklch(0.46 0.012 252) 0 3px, oklch(0.3 0.014 252) 3px 6px)',
    label: 'DEGRADED'
  },

  // Type scale & radii / spacing
  type: {
    label: '11px',
    labelLetterSpacing: '0.1em',
    chipLabelLetterSpacing: {
      small: '0.06em',
      medium: '0.08em',
      large: '0.04em'
    },
    body: '13px',
    bodyWeight: 500,
    metricMono: '19px',
    hero: '30px',
    brand: {
      concorde: { size: '13px', weight: 600, letterSpacing: '0.14em' },
      console: { size: '11px', weight: 400 }
    },
    lineHeight: 1.45
  },

  radii: {
    panel: '2px',
    bar: '1px',
    dot: '50%'
  },

  spacing: {
    headerPadding: '10px 20px',
    headerGap: '20px',
    navTab: '9px 14px',
    sectionHead: '8px 12px',
    listRow: '7px 12px',
    tableCell: '6px 10px',
    panelPadding: '14px 18px 22px',
    gridGap: '0 12px',
    waveformLane: '66px',
    confidenceTrace: '84px',
    laneLabelColumn: '84px'
  },

  motion: {
    functional: true,
    prefersReducedMotion: 'reduce'
  },

  // helpers
  verdictOf(isSynthetic: boolean, confidence: number) {
    if (isSynthetic && confidence >= 0.7) return 'synthetic'
    if (!isSynthetic && confidence >= 0.7) return 'verified'
    return 'review'
  },

  toneFor(state: SemanticStateKey) {
    const s = this.semantic[state]
    return { label: s.label, color: s.color, dim: s.dim, wash: s.wash }
  },

  // formatters
  f2(n: number) {
    return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  },
  pct(n: number) {
    return `${(n * 100).toFixed(1)}%`
  },
  ms(n: number) {
    return `${n.toLocaleString('en-US')} ms`
  },
  secs(n: number) {
    return `${n.toFixed(2)}s`
  },
  mmss(totalSeconds: number) {
    const s = Math.floor(totalSeconds % 60)
    const m = Math.floor(totalSeconds / 60)
    return `${m}:${s.toString().padStart(2, '0')}`
  }
} as const

export type Tokens = typeof tokens

// Typed design tokens and helpers for Concorde console
export const FONTS = {
  BODY: '"IBM Plex Sans", system-ui, sans-serif',
  MONO: '"IBM Plex Mono", monospace',
  FEATURE_SETTINGS: '"tnum" 1, "lnum" 1'
} as const

export const SURFACES = {
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

export const LINES = {
  LINE: 'oklch(0.3 0.016 252)',
  INNER_A: 'oklch(0.28 0.016 252)',
  INNER_B: 'oklch(0.25 0.015 252)',
  ROW_DIV: 'oklch(0.24 0.014 252)',
  CHIP_BORDER: 'oklch(0.32 0.016 252)'
} as const

export const INK = {
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

export const SEMANTIC = {
  VERIFIED: {
    COLOR: 'oklch(0.74 0.12 168)',
    DIM: 'oklch(0.42 0.07 168)',
    WASH: 'oklch(0.27 0.035 168)'
  },
  REVIEW: {
    COLOR: 'oklch(0.79 0.13 78)',
    DIM: 'oklch(0.44 0.08 78)',
    WASH: 'oklch(0.28 0.04 78)'
  },
  SYNTHETIC: {
    COLOR: 'oklch(0.66 0.17 24)',
    DIM: 'oklch(0.40 0.09 24)',
    WASH: 'oklch(0.27 0.045 24)'
  }
} as const

export const DEGRADED = {
  HATCH: 'repeating-linear-gradient(135deg, oklch(0.46 0.012 252) 0 3px, oklch(0.3 0.014 252) 3px 6px)',
  LABEL: 'DEGRADED'
} as const

export const TYPE = {
  LABEL_XS: '11px',
  LABEL_SM: '11px',
  BODY: '13px',
  METRIC: '19px',
  HERO: '30px',
  WEIGHTS: {
    REG: 400,
    MED: 500,
    SEMI: 600
  },
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

export const SPACING = {
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

export const MOTION = {
  REDUCED: '(prefers-reduced-motion: reduce)'
} as const

export const BRAND = {
  NAME: 'CONCORDE',
  SUITE: 'CONSOLE'
} as const

// Helpers
export function verdictOf(isSynthetic: boolean, confidence: number) {
  if (isSynthetic && confidence >= 0.7) return 'synthetic'
  if (!isSynthetic && confidence >= 0.7) return 'verified'
  return 'review'
}

export function toneFor(state: 'verified' | 'review' | 'synthetic') {
  switch (state) {
    case 'verified':
      return { label: 'VERIFIED', color: SEMANTIC.VERIFIED.COLOR, dim: SEMANTIC.VERIFIED.DIM, wash: SEMANTIC.VERIFIED.WASH }
    case 'synthetic':
      return { label: 'SYNTHETIC', color: SEMANTIC.SYNTHETIC.COLOR, dim: SEMANTIC.SYNTHETIC.DIM, wash: SEMANTIC.SYNTHETIC.WASH }
    default:
      return { label: 'REVIEW', color: SEMANTIC.REVIEW.COLOR, dim: SEMANTIC.REVIEW.DIM, wash: SEMANTIC.REVIEW.WASH }
  }
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

export default {
  FONTS,
  SURFACES,
  LINES,
  INK,
  SEMANTIC,
  DEGRADED,
  TYPE,
  RADII,
  SPACING,
  BRAND
}

