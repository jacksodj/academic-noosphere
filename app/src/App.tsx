import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { apiConfig } from "./api";
import { getSettings, subscribeSpend } from "./endpoints";
import type { Settings as SettingsModel, SpendSummary } from "./types";
import Dashboard from "./views/Dashboard";
import Explorer from "./views/Explorer";
import Onboarding from "./views/Onboarding";
import Report from "./views/Report";
import Settings from "./views/Settings";
import Triage from "./views/Triage";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/triage", label: "Triage", end: false },
  { to: "/report", label: "Report", end: false },
  { to: "/explorer", label: "Explorer", end: false },
  { to: "/settings", label: "Settings", end: false },
];

function SpendMeter() {
  const [spend, setSpend] = useState<SpendSummary | null>(null);

  useEffect(() => subscribeSpend(setSpend), []);

  const title = spend
    ? Object.entries(spend.models)
        .map(([model, usage]) => `${model}: $${usage.est_usd.toFixed(2)}`)
        .join("\n") || "No LLM spend yet"
    : "Estimated LLM spend";
  return (
    <span className="spend-meter" title={title}>
      est. spend <strong>{spend ? `$${spend.total.est_usd.toFixed(2)}` : "$—"}</strong>
    </span>
  );
}

export default function App() {
  // First-start gate: until settings load we render the normal shell (views
  // handle their own errors when no core is configured); once loaded, an
  // un-onboarded install sees the wizard instead of the app.
  const [settings, setSettings] = useState<SettingsModel | null>(null);
  const [settingsFailed, setSettingsFailed] = useState(false);

  useEffect(() => {
    getSettings()
      .then(setSettings)
      .catch(() => setSettingsFailed(true));
  }, []);

  if (!settingsFailed && settings === null) {
    return null; // brief; avoids flashing the app before the gate decides
  }
  if (settings && !settings.onboarded) {
    return <Onboarding settings={settings} onDone={setSettings} />;
  }

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Academic Noosphere</span>
        <nav>
          {NAV.map(({ to, label, end }) => (
            <NavLink key={to} to={to} end={end}>
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="topbar-right">
          <SpendMeter />
          {apiConfig.mock && <span className="badge mock-badge">mock</span>}
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/triage" element={<Triage />} />
          <Route path="/report" element={<Report />} />
          <Route path="/explorer" element={<Explorer />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
