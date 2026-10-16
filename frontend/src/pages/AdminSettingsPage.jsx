import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'

export default function AdminSettingsPage() {
  const { t } = useTranslation()
  const [settings, setSettings] = useState({
    expiryDays: '7',
    maxUploadSizeMb: '100',
    minPasswordLength: '8',
  })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState(null)

  useEffect(() => {
    api.get('/api/auth/application-settings')
      .then((data) => setSettings({
        expiryDays: data.default_expiry_days == null ? '' : String(data.default_expiry_days),
        maxUploadSizeMb: String(data.max_upload_size_mb),
        minPasswordLength: String(data.min_password_length),
      }))
      .catch((error) => setNotice({ type: 'error', message: error.message }))
      .finally(() => setLoading(false))
  }, [])

  const update = (key, value) => setSettings((current) => ({ ...current, [key]: value }))

  const save = async (event) => {
    event.preventDefault()
    setSaving(true)
    setNotice(null)
    try {
      await api.put('/api/auth/application-settings', {
        body: {
          expires_in_days: settings.expiryDays === '' ? null : Number(settings.expiryDays),
          max_upload_size_mb: Number(settings.maxUploadSizeMb),
          min_password_length: Number(settings.minPasswordLength),
        },
      })
      setNotice({ type: 'success', message: t('adminSettings.saved') })
    } catch (error) {
      setNotice({ type: 'error', message: error.message })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-black text-slate-900">{t('adminSettings.title')}</h1>
        <p className="text-sm text-slate-500 mt-1">{t('adminSettings.subtitle')}</p>
      </div>

      {notice && (
        <div className={`rounded-xl px-4 py-3 text-sm font-medium ${notice.type === 'error' ? 'bg-red-50 text-red-700 border border-red-200' : 'bg-emerald-50 text-emerald-700 border border-emerald-200'}`}>
          {notice.type === 'error' ? '⚠ ' : '✓ '}{notice.message}
        </div>
      )}

      <form onSubmit={save} className="space-y-6">
        <section className="card space-y-5">
          <div>
            <h2 className="font-bold text-slate-800">{t('adminSettings.inviteLinks')}</h2>
            <p className="text-xs text-slate-500 mt-1">{t('adminSettings.inviteLinksDescription')}</p>
          </div>
          <div>
            <label htmlFor="invite-expiry" className="block text-xs font-bold text-slate-600 mb-1">{t('adminSettings.defaultExpiry')}</label>
            <select
              id="invite-expiry"
              value={settings.expiryDays}
              disabled={loading}
              onChange={(event) => update('expiryDays', event.target.value)}
              className="input-base max-w-sm"
            >
              <option value="1">{t('adminSettings.oneDay')}</option>
              <option value="7">{t('adminSettings.sevenDays')}</option>
              <option value="14">{t('adminSettings.fourteenDays')}</option>
              <option value="30">{t('adminSettings.thirtyDays')}</option>
              <option value="90">{t('adminSettings.ninetyDays')}</option>
              <option value="">{t('adminSettings.neverExpires')}</option>
            </select>
            <p className="text-xs text-slate-500 mt-2">{t('adminSettings.expiryHint')}</p>
          </div>
          {settings.expiryDays === '' && (
            <p className="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800">
              ⚠ {t('adminSettings.neverWarning')}
            </p>
          )}
        </section>

        <section className="card space-y-5">
          <div>
            <h2 className="font-bold text-slate-800">{t('adminSettings.uploads')}</h2>
            <p className="text-xs text-slate-500 mt-1">{t('adminSettings.uploadsDescription')}</p>
          </div>
          <div className="max-w-sm">
            <label htmlFor="max-upload-size" className="block text-xs font-bold text-slate-600 mb-1">{t('adminSettings.maxUploadSize')}</label>
            <div className="flex items-center gap-2">
              <input id="max-upload-size" type="number" min="1" max="10240" required value={settings.maxUploadSizeMb} disabled={loading} onChange={(event) => update('maxUploadSizeMb', event.target.value)} className="input-base" />
              <span className="text-sm text-slate-500">MB</span>
            </div>
          </div>
        </section>

        <section className="card space-y-5">
          <div>
            <h2 className="font-bold text-slate-800">{t('adminSettings.passwordPolicy')}</h2>
            <p className="text-xs text-slate-500 mt-1">{t('adminSettings.passwordPolicyDescription')}</p>
          </div>
          <div className="max-w-sm">
            <label htmlFor="min-password-length" className="block text-xs font-bold text-slate-600 mb-1">{t('adminSettings.minPasswordLength')}</label>
            <div className="flex items-center gap-2">
              <input id="min-password-length" type="number" min="6" max="128" required value={settings.minPasswordLength} disabled={loading} onChange={(event) => update('minPasswordLength', event.target.value)} className="input-base" />
              <span className="text-sm text-slate-500">{t('adminSettings.characters')}</span>
            </div>
          </div>
        </section>

        <div className="flex justify-end">
          <button type="submit" disabled={loading || saving} className="btn-primary">
            {saving ? t('adminSettings.saving') : t('adminSettings.save')}
          </button>
        </div>
      </form>
    </div>
  )
}
