import React, { createContext, useContext, useEffect, useState } from "react";

type Health = {
  model: string;
  contract: string;
  p95_ms: number;
  uptime_s: number;
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
        const res = await fetch("/health");
        if (!res.ok) throw new Error("health fetch failed");
        const body = await res.json();
        if (!mounted) return;
        // replace or merge with previous safely
        setHealth(prev => ({ ...(prev ?? {}), ...(body ?? {}) } as Health));
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

