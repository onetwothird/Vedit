import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import Home from "./screens/Home";
import Import from "./screens/Import";
import Pick from "./screens/Pick";
import Placeholder from "./screens/Placeholder";

// HashRouter: a reload inside the desktop app never asks the file server for a route.
export default function AppRouter() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/p/:id/import" element={<Import />} />
        <Route path="/p/:id/pick" element={<Pick />} />
        <Route path="/p/:id/edit/:hid" element={<Placeholder titleKey="placeholder.edit" step={3} />} />
        <Route path="/p/:id/export" element={<Placeholder titleKey="placeholder.export" step={4} />} />
        <Route path="/settings" element={<Placeholder titleKey="placeholder.settings" />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  );
}
