import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { Spin } from 'antd'
import LoginPage from './pages/Login'
import AdminLayout from './layouts/AdminLayout'
import Dashboard from './pages/Dashboard'
import UsersPage from './pages/Users'
import ResourcesPage from './pages/Resources'
import KnowledgePage from './pages/Knowledge'
import MonitorPage from './pages/Monitor'
import { authApi, getSavedUser, getToken, userApi, type AdminUser } from './api'

function RequireAuth({ children }: { children: JSX.Element }) {
  const location = useLocation()
  const token = getToken()
  if (!token) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }
  return children
}

function RequireAdmin({ children }: { children: JSX.Element }) {
  const [user, setUser] = useState<AdminUser | null>(getSavedUser())
  const [loading, setLoading] = useState(!getSavedUser())

  useEffect(() => {
    const saved = getSavedUser()
    if (saved) {
      // 非 admin 拦截
      if (saved.role !== 'admin') {
        authApi.logout()
        window.location.href = '/admin/login?forbidden=1'
        return
      }
      setUser(saved)
      setLoading(false)
      return
    }
    userApi
      .me()
      .then((me) => {
        if (me.role !== 'admin') {
          authApi.logout()
          window.location.href = '/admin/login?forbidden=1'
          return
        }
        localStorage.setItem('fast_admin_user', JSON.stringify(me))
        setUser(me)
      })
      .catch(() => {
        authApi.logout()
        window.location.href = '/admin/login'
      })
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
        <Spin size="large" tip="加载中..." />
      </div>
    )
  }
  if (!user || user.role !== 'admin') return null
  return children
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <RequireAdmin>
              <AdminLayout />
            </RequireAdmin>
          </RequireAuth>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="users" element={<UsersPage />} />
        <Route path="resources" element={<ResourcesPage />} />
        <Route path="knowledge" element={<KnowledgePage />} />
        <Route path="monitor" element={<MonitorPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
