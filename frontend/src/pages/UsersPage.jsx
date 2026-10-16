import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../lib/api'
import { useAuth } from '../lib/auth'

const ROLE_BADGE = {
  admin: 'bg-violet-100 text-violet-800 border-violet-200',
  user: 'bg-teal-100 text-teal-800 border-teal-200',
}

function InvitationLink({ invitation, onCopy, t, locale }) {
  if (!invitation) return null

  return (
    <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4 space-y-2">
      <p className="text-xs font-bold text-emerald-800">{t('usersPage.invitationLink')}</p>
      <div className="flex gap-2">
        <input
          type="text"
          readOnly
          value={invitation.link}
          className="flex-1 min-w-0 bg-white border border-emerald-300 rounded-lg px-3 py-2 text-xs font-mono text-emerald-800"
          onFocus={(event) => event.target.select()}
        />
        <button onClick={() => onCopy(invitation.link)} className="btn-secondary !text-xs !px-3">{t('usersPage.copy')}</button>
      </div>
      <p className="text-xs text-emerald-700">
        {invitation.project_id
          ? <>{t('usersPage.projectRole')}: <strong>{t(`usersPage.roles.${invitation.role}`, invitation.role)}</strong></>
          : <>{t('usersPage.globalRole')}: <strong>{t(`usersPage.roles.${invitation.role}`, invitation.role)}</strong></>}
        {invitation.expires_at && <> · {t('usersPage.expires')}: {new Date(invitation.expires_at).toLocaleString(locale)}</>}
      </p>
    </div>
  )
}

export default function UsersPage() {
  const { t, i18n } = useTranslation()
  const { user: me } = useAuth()
  const locale = i18n.language === 'de' ? 'de-DE' : 'en-GB'
  const [users, setUsers] = useState([])
  const [projects, setProjects] = useState([])
  const [creationMode, setCreationMode] = useState('invite')
  const [inviteTarget, setInviteTarget] = useState('account')
  const [accountRole, setAccountRole] = useState('user')
  const [projectInvite, setProjectInvite] = useState({ projectId: '', role: 'annotator' })
  const [form, setForm] = useState({ username: '', display_name: '', password: '', role: 'user' })
  const [invitation, setInvitation] = useState(null)
  const [errors, setErrors] = useState({})
  const [saving, setSaving] = useState(false)
  const [creatingInvitation, setCreatingInvitation] = useState(false)
  const [updatingRole, setUpdatingRole] = useState(null)
  const [toast, setToast] = useState(null)

  useEffect(() => {
    loadUsers()
    loadProjects()
  }, [])

  const loadUsers = async () => setUsers(await api.get('/api/auth/users'))

  const loadProjects = async () => {
    try {
      setProjects(await api.get('/api/sessions'))
    } catch {
      // Account administration remains available if projects cannot be loaded.
    }
  }

  const showToast = (msg, type = 'success') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  const copyLink = async (link) => {
    try {
      await navigator.clipboard.writeText(link)
      showToast(t('usersPage.copied'))
    } catch {
      showToast(t('usersPage.copyError'), 'error')
    }
  }

  const validate = () => {
    const nextErrors = {}
    if (!form.username.trim()) nextErrors.username = t('usersPage.required')
    else if (!/^[a-z0-9_-]+$/.test(form.username.toLowerCase())) nextErrors.username = t('usersPage.usernameError')
    if (!form.display_name.trim()) nextErrors.display_name = t('usersPage.required')
    if (form.password.length < 6) nextErrors.password = t('usersPage.passwordError')
    return nextErrors
  }

  const handleCreate = async (event) => {
    event.preventDefault()
    const nextErrors = validate()
    if (Object.keys(nextErrors).length) {
      setErrors(nextErrors)
      return
    }
    setErrors({})
    setSaving(true)
    try {
      await api.post('/api/auth/users', { body: { ...form, username: form.username.toLowerCase() } })
      const username = form.username
      setForm({ username: '', display_name: '', password: '', role: 'user' })
      await loadUsers()
      showToast(t('usersPage.userCreated', { username }))
    } catch (error) {
      showToast(error.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleInvitation = async () => {
    if (inviteTarget === 'project' && !projectInvite.projectId) {
      showToast(t('usersPage.selectProjectFirst'), 'error')
      return
    }

    setCreatingInvitation(true)
    setInvitation(null)
    try {
      const result = inviteTarget === 'project'
        ? await api.post('/api/auth/invitations', {
          body: { project_id: Number(projectInvite.projectId), role: projectInvite.role },
        })
        : await api.post('/api/auth/account-invitations', { body: { role: accountRole } })
      setInvitation({ ...result, link: `${window.location.origin}/register?token=${result.token}` })
    } catch (error) {
      showToast(error.message, 'error')
    } finally {
      setCreatingInvitation(false)
    }
  }

  const handleRoleChange = async (username, role) => {
    setUpdatingRole(username)
    try {
      await api.patch(`/api/auth/users/${username}`, { body: { role } })
      await loadUsers()
      showToast(t('usersPage.roleUpdated', { username }))
    } catch (error) {
      showToast(error.message, 'error')
    } finally {
      setUpdatingRole(null)
    }
  }

  const handleDelete = async (username) => {
    if (!confirm(t('usersPage.deleteConfirm', { username }))) return
    try {
      await api.delete(`/api/auth/users/${username}`)
      await loadUsers()
      showToast(t('usersPage.userDeleted', { username }))
    } catch (error) {
      showToast(error.message, 'error')
    }
  }

  const chooseCreationMode = (mode) => {
    setCreationMode(mode)
    setInvitation(null)
    setErrors({})
  }

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-xl shadow-lg text-sm font-semibold ${toast.type === 'error' ? 'bg-red-600 text-white' : 'bg-emerald-600 text-white'}`}>
          {toast.msg}
        </div>
      )}

      <div>
        <h1 className="text-2xl font-black text-slate-900">{t('usersPage.title')}</h1>
        <p className="text-sm text-slate-500 mt-1">{t('usersPage.subtitle')}</p>
      </div>

      <section className="card space-y-5">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <h2 className="font-bold text-slate-800">{t('usersPage.newUser')}</h2>
          <div className="inline-flex rounded-lg bg-slate-100 p-1">
            <button
              type="button"
              onClick={() => chooseCreationMode('invite')}
              className={`px-3 py-1.5 rounded-md text-xs font-bold transition ${creationMode === 'invite' ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
            >
              {t('usersPage.invitationLink')}
            </button>
            <button
              type="button"
              onClick={() => chooseCreationMode('password')}
              className={`px-3 py-1.5 rounded-md text-xs font-bold transition ${creationMode === 'password' ? 'bg-white text-indigo-700 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
            >
              {t('usersPage.setPassword')}
            </button>
          </div>
        </div>

        {creationMode === 'invite' ? (
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-bold text-slate-600 mb-2">{t('usersPage.addUserTo')}</label>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <button
                  type="button"
                  onClick={() => { setInviteTarget('account'); setInvitation(null) }}
                  className={`rounded-xl border p-3 text-left transition ${inviteTarget === 'account' ? 'border-indigo-500 bg-indigo-50 ring-1 ring-indigo-500' : 'border-slate-200 hover:border-slate-300'}`}
                >
                  <p className="text-sm font-bold text-slate-800">{t('usersPage.accountOnly')}</p>
                  <p className="text-xs text-slate-500 mt-1">{t('usersPage.noProject')}</p>
                </button>
                <button
                  type="button"
                  onClick={() => { setInviteTarget('project'); setInvitation(null) }}
                  className={`rounded-xl border p-3 text-left transition ${inviteTarget === 'project' ? 'border-indigo-500 bg-indigo-50 ring-1 ring-indigo-500' : 'border-slate-200 hover:border-slate-300'}`}
                >
                  <p className="text-sm font-bold text-slate-800">{t('usersPage.project')}</p>
                  <p className="text-xs text-slate-500 mt-1">{t('usersPage.chooseProjectRole')}</p>
                </button>
              </div>
            </div>

            {inviteTarget === 'account' ? (
              <div className="max-w-sm">
                <label className="block text-xs font-bold text-slate-600 mb-1">{t('usersPage.globalRole')}</label>
                <select value={accountRole} onChange={(event) => setAccountRole(event.target.value)} className="input-base">
                  <option value="user">{t('usersPage.roles.user')}</option>
                  <option value="admin">{t('usersPage.roles.admin')}</option>
                </select>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-bold text-slate-600 mb-1">{t('usersPage.project')}</label>
                  <select value={projectInvite.projectId} onChange={(event) => setProjectInvite({ ...projectInvite, projectId: event.target.value })} className="input-base">
                    <option value="">{t('usersPage.selectProject')}</option>
                    {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-bold text-slate-600 mb-1">{t('usersPage.projectRole')}</label>
                  <select value={projectInvite.role} onChange={(event) => setProjectInvite({ ...projectInvite, role: event.target.value })} className="input-base">
                    <option value="annotator">{t('usersPage.roles.annotator')}</option>
                    <option value="maintainer">{t('usersPage.roles.maintainer')}</option>
                  </select>
                </div>
              </div>
            )}

            <button onClick={handleInvitation} disabled={creatingInvitation} className="btn-primary">
              {creatingInvitation ? t('usersPage.generating') : t('usersPage.generateInvite')}
            </button>
            <InvitationLink invitation={invitation} onCopy={copyLink} t={t} locale={locale} />
          </div>
        ) : (
          <form onSubmit={handleCreate} className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-bold text-slate-600 mb-1">{t('usersPage.username')} *</label>
              <input type="text" value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value.toLowerCase().replace(/\s/g, '') })} placeholder="john_doe" className={`input-base ${errors.username ? 'input-error' : ''}`} />
              {errors.username && <p className="text-xs text-red-600 mt-1">⚠ {errors.username}</p>}
            </div>
            <div>
              <label className="block text-xs font-bold text-slate-600 mb-1">{t('usersPage.displayName')} *</label>
              <input type="text" value={form.display_name} onChange={(event) => setForm({ ...form, display_name: event.target.value })} placeholder="John Doe" className={`input-base ${errors.display_name ? 'input-error' : ''}`} />
              {errors.display_name && <p className="text-xs text-red-600 mt-1">⚠ {errors.display_name}</p>}
            </div>
            <div>
              <label className="block text-xs font-bold text-slate-600 mb-1">{t('usersPage.password')} *</label>
              <input type="password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} placeholder={t('usersPage.passwordPlaceholder')} className={`input-base ${errors.password ? 'input-error' : ''}`} />
              {errors.password && <p className="text-xs text-red-600 mt-1">⚠ {errors.password}</p>}
            </div>
            <div>
              <label className="block text-xs font-bold text-slate-600 mb-1">{t('usersPage.globalRole')}</label>
              <select value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })} className="input-base">
                <option value="user">{t('usersPage.roles.user')}</option>
                <option value="admin">{t('usersPage.roles.admin')}</option>
              </select>
            </div>
            <div className="sm:col-span-2 flex justify-end">
              <button type="submit" disabled={saving} className="btn-primary">{saving ? t('usersPage.creating') : t('usersPage.createUser')}</button>
            </div>
          </form>
        )}
      </section>

      <section className="card p-0 overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-200">
          <h2 className="font-bold text-slate-800">{t('usersPage.existingUsers')}</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm whitespace-nowrap">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                {[t('usersPage.username'), t('usersPage.displayName'), t('usersPage.globalRole'), t('usersPage.created'), t('usersPage.actions')].map((heading) => <th key={heading} className="text-left px-4 py-3 text-xs font-bold text-slate-500 uppercase tracking-wide">{heading}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {users.map((user) => {
                const isCurrentUser = user.username === me?.username
                return (
                  <tr key={user.username} className="h-12 hover:bg-slate-50 transition">
                    <td className="px-4 py-3 font-mono text-slate-700">@{user.username}</td>
                    <td className="px-4 py-3 font-semibold text-slate-900">{user.display_name}</td>
                    <td className="px-4 py-3">
                      {isCurrentUser ? (
                        <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${ROLE_BADGE[user.role] ?? ''}`}>{t(`usersPage.roles.${user.role}`, user.role)}</span>
                      ) : (
                        <button
                          type="button"
                          disabled={updatingRole === user.username}
                          onClick={() => handleRoleChange(user.username, user.role === 'admin' ? 'user' : 'admin')}
                          title={t('usersPage.changeRoleTo', { role: t(`usersPage.roles.${user.role === 'admin' ? 'user' : 'admin'}`) })}
                          className={`text-xs font-bold px-2 py-0.5 rounded-full border transition hover:brightness-95 hover:shadow-sm disabled:opacity-60 ${ROLE_BADGE[user.role] ?? ''}`}
                        >
                          {updatingRole === user.username ? '…' : t(`usersPage.roles.${user.role}`, user.role)}
                        </button>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-400 text-xs">{user.created_at ? new Date(user.created_at).toLocaleDateString(locale) : '—'}</td>
                    <td className="px-4 py-3">
                      {isCurrentUser ? <span className="text-xs text-slate-400">{t('usersPage.currentUser')}</span> : <button onClick={() => handleDelete(user.username)} className="btn-danger">{t('usersPage.delete')}</button>}
                    </td>
                  </tr>
                )
              })}
              {!users.length && <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-400">{t('usersPage.noUsers')}</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
