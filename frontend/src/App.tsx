import { HashRouter, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { LiveProvider } from "./lib/live";
import { SettingsProvider } from "./lib/settings";
import { ToastProvider } from "./lib/toast";
import { Dashboard } from "./pages/Dashboard";
import { Knowledge } from "./pages/Knowledge";
import { LiveFeed } from "./pages/LiveFeed";
import { Model } from "./pages/Model";
import { MapPage } from "./pages/MapPage";
import { Nodes } from "./pages/Nodes";
import { Telemetry } from "./pages/Telemetry";
import { Trends } from "./pages/Trends";
import { Radio } from "./pages/Radio";
import { SettingsPage } from "./pages/SettingsPage";
import { Wizard } from "./pages/Wizard";

export default function App() {
  return (
    <ToastProvider>
      <SettingsProvider>
        <LiveProvider>
          <HashRouter>
            <Routes>
              <Route element={<Layout />}>
                <Route index element={<Dashboard />} />
                <Route path="feed" element={<LiveFeed />} />
                <Route path="knowledge" element={<Knowledge />} />
                <Route path="model" element={<Model />} />
                <Route path="radio" element={<Radio />} />
                <Route path="nodes" element={<Nodes />} />
                <Route path="nodes/:id" element={<Nodes />} />
                <Route path="map" element={<MapPage />} />
                <Route path="telemetry" element={<Telemetry />} />
                <Route path="trends" element={<Trends />} />
                <Route path="settings" element={<SettingsPage />} />
                <Route path="setup" element={<Wizard />} />
              </Route>
            </Routes>
          </HashRouter>
        </LiveProvider>
      </SettingsProvider>
    </ToastProvider>
  );
}
