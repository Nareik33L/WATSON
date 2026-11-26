import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { ActivityPage } from "@/pages/Activity";
import { Approvals } from "@/pages/Approvals";
import { Briefing } from "@/pages/Briefing";
import { Commitments } from "@/pages/Commitments";
import { CustomerDetail } from "@/pages/CustomerDetail";
import { Customers } from "@/pages/Customers";
import { DealDetail } from "@/pages/DealDetail";
import { Deals } from "@/pages/Deals";
import { FilesPage } from "@/pages/Files";
import { Forecast } from "@/pages/Forecast";
import { Integrations } from "@/pages/Integrations";
import { Overview } from "@/pages/Overview";
import { SettingsPage } from "@/pages/Settings";

export function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Overview />} />
          <Route path="briefing" element={<Briefing />} />
          <Route path="deals" element={<Deals />} />
          <Route path="deals/:id" element={<DealDetail />} />
          <Route path="customers" element={<Customers />} />
          <Route path="customers/:id" element={<CustomerDetail />} />
          <Route path="commitments" element={<Commitments />} />
          <Route path="approvals" element={<Approvals />} />
          <Route path="forecast" element={<Forecast />} />
          <Route path="files" element={<FilesPage />} />
          <Route path="activity" element={<ActivityPage />} />
          <Route path="integrations" element={<Integrations />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<p className="text-sm text-muted">That page is not in the command centre.</p>} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
