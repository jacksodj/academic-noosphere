import { NavLink, Route, Routes } from "react-router-dom";
import { apiConfig } from "./api";
import Dashboard from "./views/Dashboard";
import Explorer from "./views/Explorer";
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

export default function App() {
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
          {/* TODO(wave 2): live SpendMeter totals via SSE (/api/spend/events) */}
          <span className="spend-meter" title="Estimated LLM spend (placeholder)">
            est. spend <strong>$0.00</strong>
          </span>
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
