import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '@/layout/AppShell'
import { LoginPage } from '@/pages/LoginPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { SchedulePage } from '@/pages/SchedulePage'
import { OptimizerPage } from '@/pages/OptimizerPage'
import { WorkOrdersPage } from '@/pages/WorkOrdersPage'
import { ResourcesPage } from '@/pages/ResourcesPage'
import { ScenariosPage } from '@/pages/ScenariosPage'
import { AnalyticsPage } from '@/pages/AnalyticsPage'
import { AssistantPage } from '@/pages/AssistantPage'
import { AiSettingsPage } from '@/pages/AiSettingsPage'
import { useAuth } from '@/auth/useAuth'
import { Spinner } from '@/components/ui'

function Protected({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth()
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner label="Restoring session…" />
      </div>
    )
  }
  if (!user) return <Navigate to="/login" replace />
  return children
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <Protected>
            <AppShell />
          </Protected>
        }
      >
        <Route path="/" element={<DashboardPage />} />
        <Route path="/schedule" element={<SchedulePage />} />
        <Route path="/optimizer" element={<OptimizerPage />} />
        <Route path="/work-orders" element={<WorkOrdersPage />} />
        <Route path="/resources" element={<ResourcesPage />} />
        <Route path="/scenarios" element={<ScenariosPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/assistant" element={<AssistantPage />} />
        <Route path="/settings/ai" element={<AiSettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
