import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from './lib/auth'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import HomePage from './pages/HomePage'
import ConfigPage from './pages/ConfigPage'
import WorkspacePage from './pages/WorkspacePage'
import ReviewPage from './pages/ReviewPage'
import UsersPage from './pages/UsersPage'
import AdminSettingsPage from './pages/AdminSettingsPage'
import ProfilePage from './pages/ProfilePage'

/** Redirect to /login if not authenticated. */
function RequireAuth({ children }) {
  const { user } = useAuth()
  return user ? children : <Navigate to="/login" replace />
}

/** Redirect to / if not admin. */
function RequireAdmin({ children }) {
  const { user, isAdmin } = useAuth()
  if (!user) return <Navigate to="/login" replace />
  if (!isAdmin) return <Navigate to="/" replace />
  return children
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<HomePage />} />
        <Route path="config" element={<ConfigPage />} />
        <Route path="config/:sessionId" element={<ConfigPage />} />
        <Route path="workspace/:sessionId" element={<WorkspacePage />} />
        <Route path="review/:sessionId" element={<ReviewPage />} />
        <Route
          path="users"
          element={
            <RequireAdmin>
              <UsersPage />
            </RequireAdmin>
          }
        />
        <Route
          path="admin/settings"
          element={
            <RequireAdmin>
              <AdminSettingsPage />
            </RequireAdmin>
          }
        />
        <Route path="profile" element={<ProfilePage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
