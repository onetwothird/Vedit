import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import Batch from "./screens/Batch";
import Edit from "./screens/Edit";
import Export from "./screens/Export";
import Home from "./screens/Home";
import Import from "./screens/Import";
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
        <Route path="/p/:id/import" element={<Import />} />
        <Route path="/p/:id/pick" element={<Pick />} />
        <Route path="/p/:id/edit/:hid" element={<Edit />} />
        <Route path="/p/:id/export" element={<Export />} />
        <Route path="/s/:sid" element={<Story />} />
        <Route path="/batch" element={<Batch />} />
        <Route path="/batch/:bid" element={<Batch />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  );
}
