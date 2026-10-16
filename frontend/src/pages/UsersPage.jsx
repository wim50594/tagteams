import { useState, useEffect } from 'react'
import { api } from '../lib/api'

export default function UsersPage() {
  const [users, setUsers] = useState([])
  const [form, setForm] = useState({ username: '', display_name: '', password: '', role: 'user' })
  const [errors, setErrors] = useState({})
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState(null)

  // Invitation state
  const [projects, setProjects] = useState([])
  const [inviteProjectId, setInviteProjectId] = useState('')
  const [inviteRole, setInviteRole] = useState('annotator')  // project-level role
  const [inviteToken, setInviteToken] = useState(null)
  const [inviting, setInviting] = useState(false)

  useEffect(() => {
    loadUsers()
    loadProjects()
  }, [])

  const loadUsers = async () => {
    const data = await api.get('/api/auth/users')
    setUsers(data)
  }

  const loadProjects = async () => {
    try {
      const data = await api.get('/api/sessions')
      setProjects(data)
    } catch { /* non-critical */ }
  }

  const showToast = (msg, type = 'success') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  const validate = () => {
    const e = {}
    if (!form.username.trim()) e.username = 'Required'
    else if (!/^[a-z0-9_\-]+$/.test(form.username.toLowerCase())) e.username = 'Lowercase letters, numbers, _ and - only'
    if (!form.display_name.trim()) e.display_name = 'Required'
    if (form.password.length < 6) e.password = 'At least 6 characters'
    return e
  }

  const handleCreate = async (e) => {
    e.preventDefault()
    const e_ = validate()
    if (Object.keys(e_).length) { setErrors(e_); return }
    setErrors({})
    setSaving(true)
    try {
      await api.post('/api/auth/users', { body: { ...form, username: form.username.toLowerCase() } })
      setForm({ username: '', display_name: '', password: '', role: 'user' })
      await loadUsers()
      showToast(`User "${form.username}" created.`)
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (username) => {
    if (!confirm(`Delete user "${username}"? This cannot be undone.`)) return
    try {
      await api.delete(`/api/auth/users/${username}`)
      await loadUsers()
      showToast(`User "${username}" deleted.`)
    } catch (err) {
      showToast(err.message, 'error')
    }
  }

  const handleInvite = async () => {
    setInviting(true)
    try {
      const res = await api.post('/api/auth/invitations', {
        body: {
          project_id: inviteProjectId ? Number(inviteProjectId) : null,
          role: inviteRole,
        },
      })
      const link = `${window.location.origin}/register?token=${res.token}`
      setInviteToken({ link, ...res })
      showToast('Invitation link generated.')
    } catch (err) {
      showToast(err.message, 'error')
    } finally {
      setInviting(false)
    }
  }

  const ROLE_BADGE = {
    admin: 'bg-violet-100 text-violet-800 border-violet-200',
    user:  'bg-teal-100 text-teal-800 border-teal-200',
  }

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      {/* Toast */}
      {toast && (
        <div className={`fixed top-4 right-4 z-50 px-4 py-3 rounded-xl shadow-lg text-sm font-semibold
          ${toast.type === 'error' ? 'bg-red-600 text-white' : 'bg-emerald-600 text-white'}`}>
          {toast.msg}
        </div>
      )}

      <div>
        <h1 className="text-2xl font-black text-slate-900">User Management</h1>
        <p className="text-sm text-slate-500 mt-1">Create accounts and generate invitation links.</p>
      </div>

      {/* ── Account invitation ── */}
      <div className="card space-y-4">
        <div>
          <h2 className="font-bold text-slate-800 text-base">🔗 Create account invitation</h2>
          <p className="text-xs text-slate-500 mt-1">
            The recipient sets their own password. Optionally, their new account is added directly to a project.
          </p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs font-bold text-slate-600 mb-1">Project (optional)</label>
            <select
              value={inviteProjectId}
              onChange={(e) => setInviteProjectId(e.target.value)}
              className="input-base"
            >
              <option value="">No project</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-600 mb-1">Project role</label>
            <select
              value={inviteRole}
              disabled={!inviteProjectId}
              onChange={(e) => setInviteRole(e.target.value)}
              className="input-base disabled:bg-slate-100 disabled:text-slate-400"
            >
              <option value="annotator">Annotator</option>
              <option value="maintainer">Maintainer</option>
            </select>
          </div>
          <div className="flex items-end">
            <button onClick={handleInvite} disabled={inviting} className="btn-primary w-full">
              {inviting ? 'Generating…' : 'Generate account link'}
            </button>
          </div>
        </div>
        <p className="text-xs text-slate-500">New links use the expiry configured in Administration settings.</p>

        {inviteToken && (
          <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4 space-y-2">
            <p className="text-xs font-bold text-emerald-800">✅ Account invitation link (copy and send to the recipient):</p>
            <div className="flex gap-2">
              <input
                type="text"
                readOnly
                value={inviteToken.link}
                className="flex-1 bg-white border border-emerald-300 rounded-lg px-3 py-2 text-xs font-mono text-emerald-800"
                onFocus={(e) => e.target.select()}
              />
              <button
                onClick={() => { navigator.clipboard.writeText(inviteToken.link); showToast('Copied!') }}
                className="btn-secondary !text-xs !px-3"
              >
                Copy
              </button>
            </div>
            <p className="text-xs text-emerald-600">
              {inviteToken.project_id ? <>Project role: <strong>{inviteToken.role}</strong> · </> : 'No project · '}
              {inviteToken.expires_at
                ? <>Expires: {new Date(inviteToken.expires_at).toLocaleString()}</>
                : <>Does not expire</>}
            </p>
          </div>
        )}
      </div>

      {/* Create form */}
      <div className="card space-y-5">
        <h2 className="font-bold text-slate-800 text-base">Create new user</h2>
        <form onSubmit={handleCreate} className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-bold text-slate-600 mb-1">Username *</label>
            <input
              type="text"
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value.toLowerCase().replace(/\s/g, '') })}
              placeholder="john.doe"
              className={`input-base ${errors.username ? 'input-error' : ''}`}
            />
            {errors.username && <p className="text-xs text-red-600 mt-1">⚠ {errors.username}</p>}
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-600 mb-1">Display name *</label>
            <input
              type="text"
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              placeholder="John Doe"
              className={`input-base ${errors.display_name ? 'input-error' : ''}`}
            />
            {errors.display_name && <p className="text-xs text-red-600 mt-1">⚠ {errors.display_name}</p>}
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-600 mb-1">Password *</label>
            <input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              placeholder="min. 6 characters"
              className={`input-base ${errors.password ? 'input-error' : ''}`}
            />
            {errors.password && <p className="text-xs text-red-600 mt-1">⚠ {errors.password}</p>}
          </div>
          <div>
            <label className="block text-xs font-bold text-slate-600 mb-1">Role</label>
            <select
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value })}
              className="input-base"
            >
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <div className="sm:col-span-2 flex justify-end">
            <button type="submit" disabled={saving} className="btn-primary">
              {saving ? 'Creating…' : '＋ Create user'}
            </button>
          </div>
        </form>
      </div>

      {/* User list */}
      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 border-b border-slate-200">
            <tr>
              {['Username', 'Display name', 'Role', 'Created', ''].map((h) => (
                <th key={h} className="text-left px-4 py-3 text-xs font-bold text-slate-500 uppercase tracking-wide">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {users.map((u) => (
              <tr key={u.username} className="hover:bg-slate-50 transition">
                <td className="px-4 py-3 font-mono text-slate-700">@{u.username}</td>
                <td className="px-4 py-3 font-semibold text-slate-900">{u.display_name}</td>
                <td className="px-4 py-3">
                  <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${ROLE_BADGE[u.role] ?? ''}`}>
                    {u.role}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-400 text-xs">
                  {u.created_at ? new Date(u.created_at).toLocaleDateString() : '—'}
                </td>
                <td className="px-4 py-3">
                  {u.username !== me?.username && (
                    <button onClick={() => handleDelete(u.username)} className="btn-danger">
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {!users.length && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-400">No users yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
