import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'
import { APP_NAME } from '../lib/constants'

export default function RegisterPage() {
  const navigate = useNavigate()
  const { setAuth } = useAuth()
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')

  const [inviteInfo, setInviteInfo] = useState(null)
  const [isNewSetup, setIsNewSetup] = useState(false)
  const [needsInvite, setNeedsInvite] = useState(false)
  const [form, setForm] = useState({ username: '', display_name: '', password: '', password2: '' })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const init = async () => {
      if (token) {
        try {
          const info = await api.get(`/api/auth/invitations/${token}`)
          setInviteInfo(info)
        } catch {
          setError(t('auth.inviteToken'))
        }
      } else {
        try {
          const check = await api.get('/api/auth/check-setup')
          if (!check.has_users) {
            setIsNewSetup(true)
          } else {
            setNeedsInvite(true)
          }
        } catch {
          setIsNewSetup(true)
        }
      }
    }
    init()
  }, [token])

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    if (form.password !== form.password2) {
      setError('Passwords do not match.')
      return
    }
    setLoading(true)
    try {
      if (isNewSetup) {
        const body = {
          username: form.username.trim().toLowerCase(),
          display_name: form.display_name.trim(),
          password: form.password,
        }
        const data = await api.post('/api/auth/bootstrap-register', { body })
        setAuth(data.access_token, data.user)
        navigate('/')
      } else if (token && inviteInfo?.valid) {
        const data = await api.post('/api/auth/register', {
          body: {
            token,
            username: form.username.trim().toLowerCase(),
            display_name: form.display_name.trim(),
            password: form.password,
          },
        })
        setAuth(data.access_token, data.user)
        navigate('/')
      }
    } catch (err) {
      setError(err.message || 'Registration failed')
      setLoading(false)
    }
  }

  const title = isNewSetup
    ? 'Set up your admin account'
    : inviteInfo?.valid && inviteInfo.invitation_type === 'project'
      ? `Join "${inviteInfo.project_name}" as ${inviteInfo.role}`
      : inviteInfo?.valid
        ? 'Create your account'
        : t('auth.register')

  if (needsInvite) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-100 to-indigo-50 px-4">
        <div className="w-full max-w-sm text-center">
          <span className="text-5xl">🏷️</span>
          <h1 className="text-2xl font-black text-indigo-600 mt-2">{APP_NAME}</h1>
          <p className="text-slate-500 mt-4 text-sm">{t('auth.needsInvite')}</p>
          <button
            onClick={() => navigate('/login')}
            className="mt-6 text-indigo-600 font-semibold text-sm hover:underline"
          >
            ← {t('auth.loginHere')}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-100 to-indigo-50 px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <span className="text-5xl">🏷️</span>
          <h1 className="text-2xl font-black text-indigo-600 mt-2">{APP_NAME}</h1>
          <p className="text-sm text-slate-500 mt-1">{title}</p>
          {inviteInfo?.valid && inviteInfo.invitation_type === 'project' && (
            <p className="text-xs text-emerald-600 font-medium mt-1">
              Invited as <strong>{inviteInfo.role}</strong>
            </p>
          )}
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 mb-4 text-sm text-red-700 font-medium">
            ⚠ {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="card space-y-4">
          <div>
            <label className="block text-xs font-bold text-slate-600 mb-1 uppercase tracking-wide">
              {t('auth.username')}
            </label>
            <input
              type="text"
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value.toLowerCase().replace(/\s/g, '') })}
              autoFocus
              className="input-base"
              minLength={2}
              maxLength={40}
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-600 mb-1 uppercase tracking-wide">
              {t('auth.displayName')}
            </label>
            <input
              type="text"
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              className="input-base"
              minLength={1}
              maxLength={80}
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-600 mb-1 uppercase tracking-wide">
              {t('auth.password')}
            </label>
            <input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              autoComplete="new-password"
              className="input-base"
              minLength={6}
            />
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-600 mb-1 uppercase tracking-wide">
              Confirm password
            </label>
            <input
              type="password"
              value={form.password2}
              onChange={(e) => setForm({ ...form, password2: e.target.value })}
              autoComplete="new-password"
              className={`input-base ${form.password2 && form.password !== form.password2 ? 'border-red-300 bg-red-50' : ''}`}
              minLength={6}
            />
            {form.password2 && form.password !== form.password2 && (
              <p className="text-xs text-red-600 mt-1">Passwords do not match</p>
            )}
          </div>

          <button
            type="submit"
            disabled={loading || !form.username || !form.display_name || form.password.length < 6 || form.password !== form.password2}
            className="btn-primary w-full justify-center py-2.5"
          >
            {loading ? '…' : isNewSetup ? 'Create admin account' : t('auth.registerButton')}
          </button>

          {!isNewSetup && !token && !needsInvite && (
            <p className="text-center text-sm">
              <button
                type="button"
                onClick={() => navigate('/login')}
                className="text-indigo-600 font-semibold hover:underline"
              >
                ← {t('auth.loginHere')}
              </button>
            </p>
          )}
        </form>
      </div>
    </div>
  )
}
