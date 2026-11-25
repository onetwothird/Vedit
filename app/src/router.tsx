import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import RequireAuth from "./auth/RequireAuth";
import Batch from "./screens/Batch";
import Edit from "./screens/Edit";
import Export from "./screens/Export";
import Home from "./screens/Home";
import Import from "./screens/Import";
import Legal from "./screens/Legal";
import Pick from "./screens/Pick";
import Settings from "./screens/Settings";
import SignIn from "./screens/SignIn";
import Story from "./screens/Story";

// HashRouter: a reload inside the desktop app never asks the file server for a route.
export default function AppRouter() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/signin" element={<SignIn />} />
        <Route path="/terms" element={<Legal page="terms" />} />
        <Route path="/privacy" element={<Legal page="privacy" />} />
        <Route path="/p/:id/import" element={<RequireAuth><Import /></RequireAuth>} />
        <Route path="/p/:id/pick" element={<RequireAuth><Pick /></RequireAuth>} />
        <Route path="/p/:id/edit/:hid" element={<RequireAuth><Edit /></RequireAuth>} />
        <Route path="/p/:id/export" element={<RequireAuth><Export /></RequireAuth>} />
        <Route path="/s/:sid" element={<RequireAuth><Story /></RequireAuth>} />
        <Route path="/batch" element={<RequireAuth><Batch /></RequireAuth>} />
        <Route path="/batch/:bid" element={<RequireAuth><Batch /></RequireAuth>} />
        <Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  );
}
