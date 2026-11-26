import React, { useEffect, useState, useRef } from 'react';
import { NavLink } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bell,
  Compass,
  Info,
  Menu,
  ShieldCheck,
  Waves,
  X,
} from 'lucide-react';
import { INITIAL_API_STATUS, deriveApiHealthStatus } from '../utils/healthStatus';

export default function Navbar() {
  const [apiStatus, setApiStatus] = useState(INITIAL_API_STATUS);
  const [activeAlertsCount, setActiveAlertsCount] = useState(0);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const isFetchingAlertsRef = useRef(false);

  useEffect(() => {
    let isMounted = true;

    // Check health
    const checkHealth = async () => {
      try {
        const res = await fetch('/api/v1/health');
        let data = null;
        try {
          data = await res.json();
        } catch {
          data = null;
        }
        if (isMounted) {
          setApiStatus(deriveApiHealthStatus({ ok: res.ok, data }));
        }
      } catch {
        if (isMounted) {
          setApiStatus({ state: 'offline', message: 'API Offline' });
        }
      }
    };

    // Check alerts count (with overlap protection)
    const checkAlertsCount = async () => {
      if (isFetchingAlertsRef.current) return;
      isFetchingAlertsRef.current = true;
      try {
        const res = await fetch('/api/v1/alerts/summary');
        if (res.ok) {
          const data = await res.json();
          if (isMounted && data && typeof data.total_active_alerts === 'number') {
            setActiveAlertsCount(data.total_active_alerts);
          }
        }
      } catch {
        // Silently preserve current count on transient background network failure
      } finally {
        isFetchingAlertsRef.current = false;
      }
    };

    // Initial mount checks
    checkHealth();
    checkAlertsCount();

    // 60-second periodic telemetry & health polling
    const intervalId = setInterval(() => {
      checkHealth();
      checkAlertsCount();
    }, 60000);

    // Browser tab focus revalidation
    const handleFocus = () => {
      checkHealth();
      checkAlertsCount();
    };
    window.addEventListener('focus', handleFocus);

    return () => {
      isMounted = false;
      clearInterval(intervalId);
      window.removeEventListener('focus', handleFocus);
    };
  }, []);

  const navItems = [
    { label: 'Home', to: '/', end: true },
    { label: 'Monitoring Map', to: '/map', icon: Compass },
    { label: 'Rainfall Risk', to: '/risk', icon: AlertTriangle },
    { label: 'Risk Alerts', to: '/alerts', icon: Bell, badge: activeAlertsCount },
    { label: 'Historical Analytics', to: '/history', icon: BarChart3 },
    { label: 'Data Monitoring', to: '/monitoring', icon: Activity },
    { label: 'About FloodWatch', to: '/about', icon: Info },
  ];

  return (
    <header className="bg-white border-b border-slate-200 sticky top-0 z-40">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        <div className="flex items-center gap-6">
          <NavLink to="/" className="flex items-center gap-2.5 font-bold text-lg text-slate-900 tracking-tight">
            <div className="w-8 h-8 rounded-lg bg-sky-600 flex items-center justify-center text-white shadow-sm">
              <Waves className="w-5 h-5" />
            </div>
            <span>Flood<span className="text-sky-600">Watch</span></span>
          </NavLink>

          {/* Desktop Navigation */}
          <nav className="hidden xl:flex items-center gap-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    `px-3 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center gap-1.5 ${
                      isActive
                        ? 'bg-sky-50 text-sky-700 font-semibold'
                        : 'text-slate-600 hover:text-slate-900 hover:bg-slate-50'
                    }`
                  }
                >
                  {Icon && <Icon className="w-4 h-4" />}
                  <span>{item.label}</span>
                  {item.badge !== undefined && item.badge > 0 && (
                    <span className="ml-1 px-1.5 py-0.2 rounded-full text-[10px] font-bold bg-rose-500 text-white animate-pulse">
                      {item.badge}
                    </span>
                  )}
                </NavLink>
              );
            })}
          </nav>
        </div>

        {/* Right side items */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-xs">
            <span
              className={`w-2 h-2 rounded-full ${
                apiStatus.state === 'online'
                  ? 'bg-emerald-500 animate-pulse'
                  : apiStatus.state === 'checking' || apiStatus.state === 'degraded'
                  ? 'bg-amber-400'
                  : 'bg-rose-500'
              }`}
            />
            <span className="text-slate-600 font-mono hidden sm:inline">{apiStatus.message}</span>
          </div>

          <span className="hidden md:flex items-center gap-1 text-xs text-slate-500 bg-slate-100 px-2.5 py-1 rounded-full border border-slate-200">
            <ShieldCheck className="w-3.5 h-3.5 text-slate-600" />
            Odisha Pilot
          </span>

          {/* Mobile hamburger button */}
          <button
            type="button"
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="xl:hidden p-2 rounded-md text-slate-600 hover:text-slate-900 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-sky-500"
            aria-label="Toggle Navigation Menu"
          >
            {mobileMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>
        </div>
      </div>

      {/* Mobile navigation dropdown */}
      {mobileMenuOpen && (
        <div className="xl:hidden border-t border-slate-200 bg-white px-4 py-3 space-y-1 shadow-lg">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={() => setMobileMenuOpen(false)}
                className={({ isActive }) =>
                  `flex items-center justify-between px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-sky-50 text-sky-700 font-semibold'
                      : 'text-slate-700 hover:bg-slate-50 hover:text-slate-900'
                  }`
                }
              >
                <div className="flex items-center gap-2.5">
                  {Icon && <Icon className="w-4 h-4 text-sky-600" />}
                  <span>{item.label}</span>
                </div>
                {item.badge !== undefined && item.badge > 0 && (
                  <span className="px-2 py-0.5 rounded-full text-xs font-bold bg-rose-500 text-white">
                    {item.badge}
                  </span>
                )}
              </NavLink>
            );
          })}
        </div>
      )}
    </header>
  );
}
