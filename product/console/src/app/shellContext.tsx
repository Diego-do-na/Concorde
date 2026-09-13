import React, { createContext, useContext, useEffect, useState } from "react";

type Health = {
  model: string;
  contract: string;
  p95_ms: number | null;
  uptime_s: number;
  deps?: Record<string, string>;
}

type ShellState = {
  health: Health | null;
  connectionMode: "WS" | "POLLING" | "OFFLINE";
  lastKnownAt: number | null;
  setConnectionMode: (m: ShellState["connectionMode"]) => void;
}

const ShellContext = createContext<ShellState | undefined>(undefined);

export const useShell = () => {
  const c = useContext(ShellContext);
  if (!c) throw new Error("useShell must be used inside ShellProvider");
  return c;
}

export const ShellProvider: React.FC<{children: React.ReactNode}> = ({children}) => {
  const [health, setHealth] = useState<Health | null>(null);
  const [connectionMode, setConnectionMode] = useState<ShellState["connectionMode"]>("POLLING");
  const [lastKnownAt, setLastKnownAt] = useState<number | null>(null);

  useEffect(() => {
    let mounted = true;
    const doFetch = async () => {
      try {
        // /health has model_version + uptime; p95 lives in /metrics
        // (routes.detect.p95_ms); the contract id lives in /version.
        const hres = await fetch("/health");
        if (!hres.ok) throw new Error("health fetch failed");
        const h = await hres.json();
        let p95: number | null = null;
        let contract: string | null = null;
        try {
          const m = await (await fetch("/metrics")).json();
          const v = m?.routes?.detect?.p95_ms;
          if (typeof v === "number") p95 = v;
        } catch {}
        try {
          const v = await (await fetch("/version")).json();
          if (typeof v?.feature_contract === "string") contract = v.feature_contract;
        } catch {}
        if (!mounted) return;
        setHealth(prev => ({
          ...(prev ?? {}),
          model: h?.model_version ?? prev?.model ?? "",
          contract: contract ?? prev?.contract ?? h?.feature_contract ?? "",
          p95_ms: p95 ?? prev?.p95_ms ?? null,
          uptime_s: typeof h?.uptime_s === "number" ? h.uptime_s : (prev?.uptime_s ?? 0),
          deps: h?.deps ?? prev?.deps ?? {},
        } as Health));
        setLastKnownAt(Date.now());
        setConnectionMode("POLLING");
      } catch (err) {
        // on failure, keep last known health but mark offline
        setConnectionMode("OFFLINE");
      }
    };
    doFetch();
    const id = setInterval(doFetch, 10_000);
    return () => { mounted = false; clearInterval(id); }
  }, []);

  return (
    <ShellContext.Provider value={{ health, connectionMode, lastKnownAt, setConnectionMode }}>
      {children}
    </ShellContext.Provider>
  )
}

