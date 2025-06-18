import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  Activity,
  BarChart3,
  Boxes,
  CalendarClock,
  Factory,
  LayoutDashboard,
  LogOut,
  Menu,
  Settings2,
  Sparkles,
  FlaskConical,
  X,
} from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/cn'
import { useAuth } from '@/auth/useAuth'

const NAV = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/schedule', label: 'Schedule', icon: CalendarClock },
  { to: '/optimizer', label: 'Optimizer', icon: Boxes },
  { to: '/work-orders', label: 'Work orders', icon: Activity },
  { to: '/resources', label: 'Resources', icon: Factory },
  { to: '/scenarios', label: 'Scenarios', icon: FlaskConical },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/assistant', label: 'AI assistant', icon: Sparkles },
]

export function AppShell() {
  const { user, logout, isAdmin } = useAuth()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)

  return (
    <div className="flex h-full min-h-screen">
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-steel-200 bg-white transition-transform lg:static lg:translate-x-0',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className="flex items-center gap-3 border-b border-steel-200 px-5 py-4">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-600 text-white">
            <Factory size={18} />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-steel-900">Production Scheduling AI</p>
            <p className="truncate text-xs text-steel-500">Constraint-aware planning</p>
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm ml-auto lg:hidden"
            onClick={() => setOpen(false)}
            aria-label="Close navigation"
          >
            <X size={16} />
          </button>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto p-3">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-accent-50 text-accent-800'
                    : 'text-steel-600 hover:bg-steel-100 hover:text-steel-900',
                )
              }
            >
              <item.icon size={17} />
              {item.label}
            </NavLink>
          ))}
          {isAdmin && (
            <NavLink
              to="/settings/ai"
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-accent-50 text-accent-800'
                    : 'text-steel-600 hover:bg-steel-100 hover:text-steel-900',
                )
              }
            >
              <Settings2 size={17} />
              AI settings
            </NavLink>
          )}
        </nav>

        <div className="border-t border-steel-200 p-3">
          <div className="flex items-center gap-3 rounded-lg px-2 py-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-steel-200 text-xs font-semibold uppercase text-steel-700">
              {user?.username?.slice(0, 2) ?? '??'}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-steel-800">{user?.full_name || user?.username}</p>
              <p className="truncate text-xs capitalize text-steel-500">{user?.role}</p>
            </div>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              title="Sign out"
              onClick={() => {
                logout()
                navigate('/login')
              }}
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      {open && (
        <div
          className="fixed inset-0 z-30 bg-steel-950/40 lg:hidden"
          onClick={() => setOpen(false)}
          aria-hidden
        />
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-steel-200 bg-white/80 px-4 py-3 backdrop-blur lg:hidden">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setOpen(true)}
            aria-label="Open navigation"
          >
            <Menu size={18} />
          </button>
          <span className="text-sm font-semibold">Production Scheduling AI</span>
        </header>
        <main className="flex-1 overflow-y-auto bg-steel-100 p-4 lg:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
