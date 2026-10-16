import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../lib/auth'
import { api } from '../lib/api'

export default function ProfilePage() {
  const { user, updateUser, setLanguage } = useAuth()
  const { t } = useTranslation()
  const [form, setForm] = useState({
    display_name: user?.display_name || '',
    username: user?.username || '',
    password: '',
    current_password: '',
  })
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)

  const handleSave = async (e) => {
    e.preventDefault()
    setSaving(true)
    setMessage(null)
    try {
      const body = {}
      if (form.display_name !== user.display_name) body.display_name = form.display_name.trim()
      if (form.username !== user.username) body.username = form.username.trim().toLowerCase()
      if (form.password) {
        body.password = form.password
        body.current_password = form.current_password
      }

      if (Object.keys(body).length === 0) {
        setMessage({ type: 'info', text: t('profile.noChanges') })
        setSaving(false)
        return
      }

      const updated = await api.put('/api/auth/profile', { body })
      updateUser(updated)
      if (updated.access_token) {
        localStorage.setItem('tt_token', updated.access_token)
      }
      setForm((prev) => ({ ...prev, password: '', current_password: '' }))
      setMessage({ type: 'success', text: t('profile.updated') })
    } catch (err) {
      setMessage({ type: 'error', text: err.message })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="max-w-lg mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-black text-slate-900">{t('profile.title')}</h1>
        <p className="text-sm text-slate-500 mt-1">{t('profile.subtitle')}</p>
      </div>

      {message && (
        <div
          className={`rounded-lg p-3 text-sm font-medium ${
            message.type === 'error'
              ? 'bg-red-50 border border-red-200 text-red-700'
              : message.type === 'info'
                ? 'bg-slate-50 border border-slate-200 text-slate-700'
                : 'bg-emerald-50 border border-emerald-200 text-emerald-700'
          }`}
        >
          {message.text}
        </div>
      )}

      <form onSubmit={handleSave} className="card space-y-5">
        <div>
          <label className="block text-xs font-bold text-slate-600 mb-1 uppercase tracking-wide">
            {t('profile.displayName')}
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
            {t('profile.username')}
          </label>
          <input
            type="text"
            value={form.username}
            onChange={(e) =>
              setForm({ ...form, username: e.target.value.toLowerCase().replace(/\s/g, '') })
            }
            className="input-base"
            minLength={2}
            maxLength={40}
          />
          <p className="text-xs text-slate-400 mt-1">{t('profile.usernameHint')}</p>
        </div>

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-bold text-slate-600 mb-1 uppercase tracking-wide">
              {t('profile.newPassword')}
            </label>
            <input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              className="input-base"
              autoComplete="new-password"
              minLength={6}
            />
            <p className="text-xs text-slate-400 mt-1">{t('profile.passwordHint')}</p>
          </div>
          {form.password && (
            <div>
              <label className="block text-xs font-bold text-slate-600 mb-1 uppercase tracking-wide">
                {t('profile.currentPassword')}
              </label>
              <input
                type="password"
                value={form.current_password}
                onChange={(e) => setForm({ ...form, current_password: e.target.value })}
                className="input-base"
                autoComplete="current-password"
                required
              />
            </div>
          )}
        </div>

        <div>
          <label className="block text-xs font-bold text-slate-600 mb-1 uppercase tracking-wide">
            {t('profile.language')}
          </label>
          <select
            value={user?.language || 'en'}
            onChange={(e) => setLanguage(e.target.value)}
            className="input-base"
          >
            <option value="en">🇬🇧 English</option>
            <option value="de">🇩🇪 Deutsch</option>
          </select>
        </div>

        <button type="submit" disabled={saving} className="btn-primary">
          {saving ? t('profile.saving') : t('profile.saveChanges')}
        </button>
      </form>
    </div>
  )
}
