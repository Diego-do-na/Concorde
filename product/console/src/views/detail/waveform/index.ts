export { default as DualChannelWaveform } from './DualChannelWaveform'
export type { DualChannelWaveformProps, ScrubState } from './DualChannelWaveform'

export { createTimeScale, LABEL_GUTTER_PX } from './TimeScale'
export type { TimeScale } from './TimeScale'

export { bucketCount, downsamplePeaks, MAX_AMPLITUDE, BAR_WIDTH_PX, BAR_GAP_PX } from './peakEnvelope'
export { interpolateConfidence } from './confidence'
export type { TimelinePoint } from './confidence'

export {
  drawLane,
  drawWaveformBars,
  drawTurnBlocks,
  drawTurnTint,
  BASELINE_COLOR,
  LANE_BORDER_COLOR
} from './drawLane'
export type { CanvasLike, Interval, DrawLaneOptions } from './drawLane'

export { useElementWidth } from './useElementWidth'
