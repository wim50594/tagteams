import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'

export default function AdminSettingsPage() {
  const { t } = useTranslation()
  const [expiryDays, setExpiryDays] = useState('7')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState(null)

  useEffect(() => {
    api.get('/api/auth/invitation-settings')
      .then((data) => setExpiryDays(data.default_expiry_days == null ? '' : String(data.default_expiry_days)))
      .catch((error) => setNotice({ type: 'error', message: error.message }))
      .finally(() => setLoading(false))
  }, [])

  const save = async (event) => {
    event.preventDefault()
    setSaving(true)
    setNotice(null)
    try {
      await api.put('/api/auth/invitation-settings', {
        body: { expires_in_days: expiryDays === '' ? null : Number(expiryDays) },
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

      <form onSubmit={save} className="card space-y-5">
        <div>
          <h2 className="font-bold text-slate-800">{t('adminSettings.inviteLinks')}</h2>
          <p className="text-xs text-slate-500 mt-1">{t('adminSettings.inviteLinksDescription')}</p>
        </div>
        <div>
          <label htmlFor="invite-expiry" className="block text-xs font-bold text-slate-600 mb-1">{t('adminSettings.defaultExpiry')}</label>
          <select
            id="invite-expiry"
            value={expiryDays}
            disabled={loading}
            onChange={(event) => setExpiryDays(event.target.value)}
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
        {expiryDays === '' && (
          <p className="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800">
            ⚠ {t('adminSettings.neverWarning')}
          </p>
        )}
        <div className="flex justify-end">
          <button type="submit" disabled={loading || saving} className="btn-primary">
            {saving ? t('adminSettings.saving') : t('adminSettings.save')}
          </button>
        </div>
      </form>
    </div>
  )
}
