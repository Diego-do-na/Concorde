import React, { createContext, useContext, useEffect, useMemo, useState } from "react";
import { Feed, FeedLike, FeedTransport, API_BASE } from "../lib/api";

/*
 * Shell-wide state: service health, and the live feed.
 *
 * Two things were wrong here and both showed up in the header.
 *
 * 1. `connectionMode` was set to "POLLING" whenever the /health poll
 *    succeeded, and "OFFLINE" whenever it failed. The chip is labelled
 *    "feed", so it was reporting on the wrong subsystem entirely: a
 *    connected websocket delivering frames still read POLLING. Feed
 *    transport now comes from `Feed.subscribeTransport`, and /health
 *    reachability is its own flag (`apiOnline`) driving the status dot.
 *
 * 2. Live and DetailView each constructed their own `Feed`, so the console
 *    held two websockets and two poll timers against the same ring. The
 *    provider owns one and hands it down.
 */

type Health = {
  model: string;
  contract: string;
  p95_ms: number | null;
  uptime_s: number;
  /** `routes.detect.count` — every /detect request the service has answered. */
  processed: number | null;
  /**
   * `fallbacks` — of those, how many returned the maximal-uncertainty
   * verdict instead of a scored one (ADR-006). A fallback is a degradation
   * and §7.2 forbids leaving one unmarked, so it is carried separately
   * rather than folded into `processed`.
   */
  fallbacks: number | null;
  deps?: Record<string, string>;
};

type ShellState = {
  health: Health | null;
  /** Is /health answering right now? Drives the OK / DEGRADED dot. */
  apiOnline: boolean;
  /** How the feed is being delivered. Drives the "feed" chip. */
  connectionMode: FeedTransport;
  lastKnownAt: number | null;
  feed: FeedLike;
};

const ShellContext = createContext<ShellState | undefined>(undefined);

export const useShell = () => {
  const c = useContext(ShellContext);
  if (!c) throw new Error("useShell must be used inside ShellProvider");
  return c;
};

/** The shared feed, for views that render it. */
export const useFeed = () => useShell().feed;

const HEALTH_POLL_MS = 10_000;

export const ShellProvider: React.FC<{ children: React.ReactNode; feed?: FeedLike }> = ({ children, feed }) => {
  const [health, setHealth] = useState<Health | null>(null);
  const [apiOnline, setApiOnline] = useState(true);
  const [connectionMode, setConnectionMode] = useState<FeedTransport>("OFFLINE");
  const [lastKnownAt, setLastKnownAt] = useState<number | null>(null);

  // `feed` is injectable so tests can drive the shell without a socket.
  const feedInstance = useMemo(() => feed ?? new Feed(), [feed]);

  useEffect(() => {
    // reopen() is a no-op on a live feed. It matters under StrictMode, which
    // runs mount → cleanup → mount: the cleanup below closed the memoized
    // instance, and without this the remounted console sat on polling forever
    // with a socket that was never reopened.
    feedInstance.reopen?.();
    const unsub = feedInstance.subscribeTransport(setConnectionMode);
    return () => {
      unsub();
      if (!feed) feedInstance.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedInstance]);

  useEffect(() => {
    let mounted = true;

    const doFetch = async () => {
      try {
        // The three chips come from three different routes: /health has
        // model_version and uptime, /metrics has the latency histogram and
        // the processed counter, /version has the feature contract.
        const hres = await fetch(`${API_BASE}/health`);
        if (!hres.ok) throw new Error("health fetch failed");
        const h = await hres.json();

        let p95: number | null = null;
        let processed: number | null = null;
        let fallbacks: number | null = null;
        let contract: string | null = null;
        // /metrics and /version are best-effort: losing either degrades one
        // chip to its last known value, it does not mark the service down.
        try {
          const m = await (await fetch(`${API_BASE}/metrics`)).json();
          const v = m?.routes?.detect?.p95_ms;
          if (typeof v === "number") p95 = v;
          const c = m?.routes?.detect?.count;
          if (typeof c === "number") processed = c;
          if (typeof m?.fallbacks === "number") fallbacks = m.fallbacks;
        } catch {}
        try {
          const v = await (await fetch(`${API_BASE}/version`)).json();
          if (typeof v?.feature_contract === "string") contract = v.feature_contract;
        } catch {}

        if (!mounted) return;
        setHealth((prev) => ({
          model: h?.model_version ?? prev?.model ?? "",
          contract: contract ?? prev?.contract ?? h?.feature_contract ?? "",
          p95_ms: p95 ?? prev?.p95_ms ?? null,
          uptime_s: typeof h?.uptime_s === "number" ? h.uptime_s : prev?.uptime_s ?? 0,
          processed: processed ?? prev?.processed ?? null,
          fallbacks: fallbacks ?? prev?.fallbacks ?? null,
          deps: h?.deps ?? prev?.deps ?? {},
        }));
        setLastKnownAt(Date.now());
        setApiOnline(true);
      } catch {
        // Keep the last known health on screen rather than blanking the
        // chips — an operator needs to know what the service *was* running
        // when it stopped answering. The dot is what says it is down.
        if (mounted) setApiOnline(false);
      }
    };

    doFetch();
    const id = setInterval(doFetch, HEALTH_POLL_MS);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, []);

  const value = useMemo(
    () => ({ health, apiOnline, connectionMode, lastKnownAt, feed: feedInstance }),
    [health, apiOnline, connectionMode, lastKnownAt, feedInstance],
  );

  return <ShellContext.Provider value={value}>{children}</ShellContext.Provider>;
};
