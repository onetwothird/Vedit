import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { isWeb } from "../engine/flavor";
import { useAuth } from "./auth";

/** The website works only for signed-in people: anyone else is sent to /signin and returns to this page afterwards.
    The desktop app has no gate (signing in is optional there). */
export default function RequireAuth({ children }: { children: ReactNode }) {
  const { account, busy } = useAuth();
  const here = useLocation();
  if (!isWeb() || account) return <>{children}</>;
  if (busy) return null; // a saved session is still being restored
  return <Navigate to="/signin" replace state={{ from: here.pathname + here.search }} />;
}
