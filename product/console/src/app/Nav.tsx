import React from "react";
import { NavLink } from "react-router-dom";
import "./nav.css";

const tabs = [
  {to: "/", label: "Monitor"},
  {to: "/calls/1", label: "Detail"},
  {to: "/exec", label: "Exec"},
  {to: "/demo", label: "Demo"},
];

export default function Nav(){
  return (
    <nav className="shell-nav" role="navigation" aria-label="Main">
      {tabs.map(t => (
        <NavLink key={t.to} to={t.to} className={({isActive})=> isActive ? "tab active" : "tab"}>
          {t.label}
        </NavLink>
      ))}
    </nav>
  )
}

