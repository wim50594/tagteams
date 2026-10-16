import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { formatVersionBadge, useVersion } from '../lib/version';

import { useAuth } from '../lib/auth';
import { APP_NAME } from '../lib/constants';

const ROLE_BADGE = {
  admin: 'bg-violet-100 text-violet-800 border-violet-200',
  user: 'bg-teal-100 text-teal-800 border-teal-200',
}

export default function Layout() {
  const { user, isAdmin, logout, setLanguage } = useAuth()
  const { t } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const version = useVersion()

  const handleLogout = () => { logout(); navigate('/login') }

  const navLink = (to, label) => (
    <Link
      to={to}
      className={`px-3 py-1.5 rounded-lg text-sm font-semibold transition
        ${location.pathname === to
          ? 'bg-indigo-600 text-white'
          : 'text-slate-500 hover:bg-slate-100'
        }`}
    >
      {label}
    </Link>
  )

  return (
    <div className="min-h-screen flex flex-col">
      {/* ── Navigation bar ── */}
      <header className="bg-white border-b border-slate-200 sticky top-0 z-40 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
          {/* Logo */}
          <Link to="/" className="flex items-center gap-2 hover:opacity-80 transition">
            <span className="text-xl">🏷️</span>
            <span className="font-black text-indigo-600 tracking-tight text-lg">{APP_NAME}</span>
            <span className="text-[10px] bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded-full font-semibold">
              {formatVersionBadge(version)}
            </span>
          </Link>

          {/* Nav links — centred */}
          <nav className="flex items-center gap-1">
            {navLink('/', t('nav.projects'))}
            {isAdmin && navLink('/users', t('nav.users'))}
            {isAdmin && navLink('/admin/settings', t('nav.settings'))}
          </nav>

          {/* Language + user badge + logout */}
          <div className="flex items-center gap-3">
            {/* Language switcher */}
            <select
              value={user?.language || 'en'}
              onChange={(e) => setLanguage(e.target.value)}
              className="text-xs border border-slate-200 rounded-lg px-2 py-1 bg-white text-slate-600 font-medium hover:border-indigo-300 focus:outline-none focus:border-indigo-400 transition"
            >
              <option value="en">🇬🇧 EN</option>
              <option value="de">🇩🇪 DE</option>
            </select>

            {/* User info */}
            <div className="flex items-center gap-2">
              <Link to="/profile" className="text-right hidden sm:block hover:opacity-80 transition">
                <p className="text-sm font-bold text-slate-800 leading-none">
                  {user?.display_name}
                </p>
                <p className="text-xs text-slate-400 leading-none mt-0.5">@{user?.username}</p>
              </Link>
              <span
                className={`text-xs font-bold px-2 py-0.5 rounded-full border ${ROLE_BADGE[user?.role] ?? ''}`}
              >
                {user?.role}
              </span>
            </div>
            <button
              onClick={handleLogout}
              className="btn-secondary !px-3 !py-1.5 !text-xs"
            >
              {t('nav.signOut')}
            </button>
          </div>
        </div>
      </header>

      {/* ── Page content ── */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 py-8">
        <Outlet />
      </main>
    </div>
  )
}