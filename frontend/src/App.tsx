import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import { RequireAuth, RequireRole } from './components/RequireAuth'
import { AuthProvider, useAuth } from './lib/AuthContext'
import { PAYMENTS_ROLES, SLAB_SCAN_ROLES } from './lib/roles'
import AuditPage from './pages/AuditPage'
import DeliveriesPage from './pages/DeliveriesPage'
import DeliveryCardPage from './pages/DeliveryCardPage'
import ScansPage from './pages/ScansPage'
import ScanSubmitPage from './pages/ScanSubmitPage'
import ScanCardPage from './pages/ScanCardPage'
import HomePage from './pages/HomePage'
import ItemCardPage from './pages/ItemCardPage'
import LoginPage from './pages/LoginPage'
import PaymentsPage from './pages/PaymentsPage'
import SubmitCheckPage from './pages/SubmitCheckPage'

/**
 * Home ("/") for most people is the switchboard. Yard users (Wade, Sam)
 * only have Slab scans, so open them straight there instead of a one-tile
 * landing page. Every entry point funnels through "/", so this one gate
 * covers fresh logins, app-open, and the "*" catch-all redirect.
 */
function HomeRoute() {
  const { user } = useAuth()
  if (user?.role === 'yard') return <Navigate to="/scans" replace />
  return <HomePage />
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            element={
              <RequireAuth>
                <Layout />
              </RequireAuth>
            }
          >
            <Route path="/" element={<HomeRoute />} />
            <Route
              path="/payments"
              element={
                <RequireRole roles={PAYMENTS_ROLES}>
                  <PaymentsPage />
                </RequireRole>
              }
            />
            <Route
              path="/payments/submit"
              element={
                <RequireRole roles={PAYMENTS_ROLES}>
                  <SubmitCheckPage />
                </RequireRole>
              }
            />
            {/* The decision card — push notifications deep-link here. */}
            <Route
              path="/payments/item/:id"
              element={
                <RequireRole roles={PAYMENTS_ROLES}>
                  <ItemCardPage />
                </RequireRole>
              }
            />
            <Route
              path="/payments/audit"
              element={
                <RequireRole roles={['admin']}>
                  <AuditPage />
                </RequireRole>
              }
            />
            {/* Slab deliveries (slab chapter) */}
            <Route
              path="/deliveries"
              element={
                <RequireRole roles={PAYMENTS_ROLES}>
                  <DeliveriesPage />
                </RequireRole>
              }
            />
            <Route
              path="/deliveries/submit"
              element={
                <RequireRole roles={PAYMENTS_ROLES}>
                  <SubmitCheckPage variant="delivery" />
                </RequireRole>
              }
            />
            <Route
              path="/deliveries/item/:id"
              element={
                <RequireRole roles={PAYMENTS_ROLES}>
                  <DeliveryCardPage />
                </RequireRole>
              }
            />
            {/* Slab scans (slab scans chapter) */}
            <Route
              path="/scans"
              element={
                <RequireRole roles={SLAB_SCAN_ROLES}>
                  <ScansPage />
                </RequireRole>
              }
            />
            <Route
              path="/scans/submit"
              element={
                <RequireRole roles={SLAB_SCAN_ROLES}>
                  <ScanSubmitPage />
                </RequireRole>
              }
            />
            <Route
              path="/scans/item/:id"
              element={
                <RequireRole roles={SLAB_SCAN_ROLES}>
                  <ScanCardPage />
                </RequireRole>
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}
