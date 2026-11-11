import { useCallback } from "react";
import { Routes, Route, Navigate, useNavigate } from "react-router-dom";

// Geo-hub pages
import GeoAppLayout from "@/components/geo/AppLayout";
import PortfolioCommand from "@/pages/PortfolioCommand";
import AssetBriefing from "@/pages/AssetBriefing";
import EvidenceExplorer from "@/pages/EvidenceExplorer";
import WorkflowBuilder from "@/pages/WorkflowBuilder";
import ROICalculator from "@/pages/ROICalculator";
import ScenarioSimulator from "@/pages/ScenarioSimulator";
import CommunityChallenges from "@/pages/CommunityChallenges";
import InsightMarketplace from "@/pages/InsightMarketplace";
import LiveMonitoring from "@/pages/LiveMonitoring";
import SatelliteTracker from "@/pages/SatelliteTracker";
import InterventionPlannerPage from "@/pages/InterventionPlannerPage";
import ProjectDetail from "@/pages/ProjectDetail";
import GeoNotFound from "@/pages/NotFound";
// import { InsightCard, type InsightData } from "@/components/visualizations/InsightCard";
// WorkflowHistoryPanel moved to Insights tab — keeping import for future use
// import { WorkflowHistoryPanel } from "@/components/sidebar/WorkflowHistoryPanel";
import { LoginPage } from "@/components/auth/LoginPage";
import { ResetPasswordPage } from "@/components/auth/ResetPasswordPage";
import { useAuth } from "@/components/auth/useAuth";

import { AuthenticatedApp } from "./AuthenticatedApp";

export default function App() {
  // No longer blocking mobile — serve adapted layout instead (mobile gets bottom-tab UI)
  return <AppInner />;
}

function AppInner() {
  // Supabase auth: session/user/accessToken come from AuthProvider context.
  // Replaces the legacy localStorage 'jonaai-token' / 'jonaai-user' pattern.
  const { session, user, accessToken, loading, signOut, passwordRecovery, clearPasswordRecovery } =
    useAuth();
  const navigate = useNavigate();
  const authToken = accessToken;
  const authUser = user?.email ?? null;

  const handleLogout = useCallback(async () => {
    await signOut();
  }, [signOut]);

  if (loading) {
    return (
      <div
        className="flex min-h-screen items-center justify-center bg-background"
        role="status"
        aria-label="Loading application"
      >
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
        <span className="sr-only">Loading...</span>
      </div>
    );
  }

  // Checked before the session gate: a recovery link signs the user in, and must
  // not drop them into the app without choosing a new password.
  if (passwordRecovery) {
    return (
      <ResetPasswordPage
        onDone={() => {
          clearPasswordRecovery();
          navigate("/", { replace: true });
        }}
      />
    );
  }

  if (!session) {
    return <LoginPage />;
  }

  return (
    <Routes>
      <Route
        path="/app"
        element={
          <AuthenticatedApp authUser={authUser} authToken={authToken} onLogout={handleLogout} />
        }
      />
      <Route element={<GeoAppLayout username={authUser} onLogout={handleLogout} />}>
        <Route path="/dashboard" element={<PortfolioCommand />} />
        <Route path="/dashboard/analysis/:analysisId" element={<AssetBriefing />} />
        <Route path="/dashboard/analysis/:analysisId/evidence" element={<EvidenceExplorer />} />
        <Route path="/dashboard/asset/:assetId" element={<AssetBriefing />} />
        <Route path="/dashboard/asset/:assetId/evidence" element={<EvidenceExplorer />} />
        <Route path="/dashboard/evidence/:assetId" element={<EvidenceExplorer />} />
        <Route path="/dashboard/workflows" element={<WorkflowBuilder />} />
        <Route path="/dashboard/roi" element={<ROICalculator />} />
        <Route path="/dashboard/scenarios" element={<ScenarioSimulator />} />
        <Route path="/dashboard/community" element={<CommunityChallenges />} />
        <Route path="/dashboard/marketplace" element={<InsightMarketplace />} />
        <Route path="/dashboard/monitoring" element={<LiveMonitoring />} />
        <Route path="/dashboard/satellites" element={<SatelliteTracker />} />
        <Route path="/dashboard/interventions" element={<InterventionPlannerPage />} />
        <Route path="/dashboard/project/:projectId" element={<ProjectDetail />} />
      </Route>
      <Route path="/" element={<Navigate to="/app" replace />} />
      <Route path="*" element={<GeoNotFound />} />
    </Routes>
  );
}

