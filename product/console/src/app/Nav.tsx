import React, { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useShell } from "./shellContext";
import "./nav.css";

/**
 * The Detail tab has no fixed URL — it is always "the call you are looking
 * at". It used to be hardcoded to `/calls/1`, an id the service has never
 * issued, so clicking the tab reliably landed on "analysis not retained".
 * It now points at the call already open, falling back to the newest one in
 * the feed.
 */
export default function Nav() {
  const { feed } = useShell();
  const location = useLocation();
  const [newestId, setNewestId] = useState<string | null>(null);

  useEffect(() => feed.subscribe((items) => setNewestId(items[0]?.id ?? null)), [feed]);

  const openCall = location.pathname.startsWith("/calls/")
    ? decodeURIComponent(location.pathname.slice("/calls/".length))
    : null;
  const detailId = openCall ?? newestId;

  const tabs = [
    { to: "/", label: "Monitor", end: true },
    {
      to: detailId ? `/calls/${encodeURIComponent(detailId)}` : "/calls",
      label: "Detail",
      // No call to open yet: the tab would lead to a dead end, so it says so
      // rather than navigating somewhere that cannot render.
      disabled: !detailId,
    },
    { to: "/exec", label: "Exec" },
    { to: "/demo", label: "Demo" },
  ];

  return (
    <nav className="shell-nav" role="navigation" aria-label="Main">
      {tabs.map((t) =>
        t.disabled ? (
          <span key={t.label} className="tab is-disabled" aria-disabled="true" title="No call to inspect yet">
            {t.label}
          </span>
        ) : (
          <NavLink
            key={t.label}
            to={t.to}
            end={t.end}
            className={({ isActive }) =>
              isActive || (t.label === "Detail" && !!openCall) ? "tab active" : "tab"
            }
          >
            {t.label}
          </NavLink>
        ),
      )}
    </nav>
  );
}
