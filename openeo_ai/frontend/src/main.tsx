import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AuthProvider } from "@/components/auth/AuthProvider";
import "./index.css";

// Convex was removed in Phase 8 of the Supabase migration.
// All data now flows through the FastAPI backend → Supabase PostgreSQL.
function AppWrapper() {
  return (
    <ErrorBoundary section="Application">
      <AuthProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </AuthProvider>
    </ErrorBoundary>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppWrapper />
  </React.StrictMode>,
);
