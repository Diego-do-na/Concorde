// Mapping from F-ids to feature keys and short one-line descriptions.
// Names MUST match product/artifacts/feature_contract_fc-1.json features (fc-1).
export const FEATURE_NOTES: Record<
  string,
  { key: string; note: string }
> = {
  "F-01": { key: "resp_latency_mean", note: "Mean response latency (caller→agent)" },
  "F-02": { key: "resp_latency_median", note: "Median response latency" },
  "F-03": { key: "resp_latency_std", note: "Response latency standard deviation" },
  "F-04": { key: "resp_latency_cv", note: "Coefficient of variation of response latency" },
  "F-05": { key: "latency_monotony_index", note: "Monotony index of latency (consistency)" },
  "F-06": { key: "resp_latency_min", note: "Minimum response latency observed" },
  "F-07": { key: "overlap_count", note: "Number of speaker overlaps" },
  "F-08": { key: "overlap_rate_per_min", note: "Overlap events per minute" },
  "F-09": { key: "overlap_total_dur", note: "Total duration of overlaps (s)" },
  "F-10": { key: "caller_bargein_count", note: "Times caller barge‑in occurred" },
  "F-11": { key: "recovery_delay_mean", note: "Mean recovery delay after interruption" },
  "F-12": { key: "recovery_delay_cv", note: "Recovery delay coefficient of variation" },
  "F-13": { key: "recovery_abort_ratio", note: "Ratio of aborted recoveries" },
  "F-14": { key: "caller_turn_dur_mean", note: "Average caller turn duration" },
  "F-15": { key: "caller_turn_dur_std", note: "Caller turn duration std dev" },
  "F-16": { key: "caller_turn_dur_cv", note: "Caller turn duration CV" },
  "F-17": { key: "short_turn_ratio", note: "Proportion of short caller turns" },
  "F-18": { key: "fragmentation_rate", note: "Turn fragmentation rate" },
  "F-19": { key: "caller_speech_ratio", note: "Caller speech occupancy ratio" },
  "F-20": { key: "speech_balance", note: "Balance of speech between parties" },
  "F-21": { key: "silence_break_delay_mean", note: "Mean silence-to-speech delay" },
  "F-22": { key: "silence_break_delay_cv", note: "Silence break delay CV" },
  "F-23": { key: "turn_count_caller", note: "Number of caller turns" },
};

