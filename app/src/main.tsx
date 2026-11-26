import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./theme/tokens.css";
import "./app.css";
import "./settings.css";
import "./export.css";
import App from "./App";
import { bootSync } from "./sync/sync";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

bootSync(); // desktop app with VITE_CLOUD_URL only: keeps projects in step with the website
