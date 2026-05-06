import { Link, useLocation } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { Bell, CreditCard, Heart, History, ImagePlus, ListTodo, Loader2, LogOut, Menu, Terminal, UserCircle, X, Zap } from 'lucide-react';
import { AccountInfo, formatBalance, getAccount, logoutAccount } from '../api';
import { useAuth } from '../auth';
import { useSite } from '../site';
import { useTasks } from '../tasks';
import AvatarBadge from './AvatarBadge';
import ModelBadge from './ModelBadge';
import jokoLogo from '../../joko.svg';

export default function TopNavBar() {
  const location = useLocation();
  const { viewer, refresh } = useAuth();
  const { siteSettings, openAnnouncement, t } = useSite();
  const { activeCount, openDrawer } = useTasks();
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    getAccount().then(setAccount).catch(() => setAccount(null));
  }, [viewer?.owner_id]);

  useEffect(() => {
    setMobileMenuOpen(false);
  }, [location.pathname]);

  async function handleLogout() {
    if (loggingOut) return;
    setLoggingOut(true);
    try {
      await logoutAccount();
    } finally {
      await refresh();
      const refreshed = await getAccount().catch(() => null);
      setAccount(refreshed);
      window.location.href = '/';
    }
  }

  const viewerLabel = viewer?.authenticated
    ? (viewer.user?.username || viewer.user?.email || 'USER')
    : t('home_guest', { value: viewer?.guest_id?.slice(0, 8) || '--' });
  const rechargeUrl = 'https://ai.get-money.locker';
  const mobileNavItems = [
    { name: t('side_generate'), path: '/', icon: Zap },
    { name: t('side_ecommerce'), path: '/ecommerce', icon: ImagePlus },
    { name: t('side_history'), path: '/history', icon: History },
    ...(viewer?.authenticated ? [{ name: t('side_favorites'), path: '/favorites', icon: Heart }] : []),
    { name: t('side_account'), path: '/account', icon: UserCircle },
    { name: t('side_config'), path: '/config', icon: Terminal },
    { name: t('side_billing'), path: '/billing', icon: CreditCard },
  ];

  return (
    <header className="fixed top-0 left-0 w-full z-[100] flex justify-between items-center px-3 sm:px-6 h-16 bg-surface-bright border-b border-primary/30 shadow-[0_0_20px_rgba(0,243,255,0.1)] shrink-0 font-mono">
      <div className="flex min-w-0 items-center gap-3 lg:gap-6 xl:gap-8">
        <button
          className="flex h-10 w-10 shrink-0 items-center justify-center border border-primary/25 text-primary transition-colors hover:bg-primary/10 lg:hidden"
          type="button"
          onClick={() => setMobileMenuOpen((current) => !current)}
          aria-label={mobileMenuOpen ? t('mobile_menu_close') : t('mobile_menu_open')}
          aria-expanded={mobileMenuOpen}
        >
          {mobileMenuOpen ? <X size={18} /> : <Menu size={18} />}
        </button>
        <div className="flex min-w-0 items-center gap-3 sm:gap-4">
          <img alt="joko-image2" className="h-10 w-10 rounded-sm object-contain" src={jokoLogo} />
          <div className="flex flex-col gap-1">
            <Link to="/" className="truncate text-xl font-black tracking-tighter text-white hover:text-primary transition-colors sm:text-2xl">
              joko-<span className="text-secondary">image2</span>
            </Link>
            <ModelBadge compact />
          </div>
        </div>
        <nav className="hidden lg:flex gap-2 xl:gap-5">
          <Link
            to="/"
            className={`text-xs uppercase tracking-widest font-bold px-3 py-2 transition-all duration-300 hover:bg-primary/10 hover:text-primary ${
              location.pathname === '/' ? 'text-primary border-b-2 border-primary' : 'text-on-surface-variant'
            }`}
          >
            {t('home_tab_general')}
          </Link>
          <Link
            to="/ecommerce"
            className={`text-xs uppercase tracking-widest font-bold px-3 py-2 transition-all duration-300 hover:bg-primary/10 hover:text-primary ${
              location.pathname === '/ecommerce' ? 'text-primary border-b-2 border-primary' : 'text-on-surface-variant'
            }`}
          >
            {t('home_tab_ecommerce')}
          </Link>
          <Link
            to="/history"
            className={`text-xs uppercase tracking-widest font-bold px-3 py-2 transition-all duration-300 hover:bg-primary/10 hover:text-primary ${
              location.pathname === '/history' ? 'text-primary border-b-2 border-primary' : 'text-on-surface-variant'
            }`}
          >
            {t('top_history')}
          </Link>
          {viewer?.authenticated ? (
            <Link
              to="/favorites"
              className={`text-xs uppercase tracking-widest font-bold px-3 py-2 transition-all duration-300 hover:bg-primary/10 hover:text-primary ${
                location.pathname === '/favorites' ? 'text-primary border-b-2 border-primary' : 'text-on-surface-variant'
              }`}
            >
              {t('top_favorites')}
            </Link>
          ) : null}
          {!viewer?.authenticated && (
            <Link
              to="/register"
              className={`text-xs uppercase tracking-widest font-bold px-3 py-2 transition-all duration-300 hover:bg-primary/10 hover:text-primary ${
                location.pathname === '/register' ? 'text-primary border-b-2 border-primary' : 'text-on-surface-variant'
              }`}
            >
              {t('top_register')}
            </Link>
          )}
        </nav>
      </div>
      <div className="flex items-center gap-2 lg:gap-3 xl:gap-6">
        <div className="hidden lg:flex items-center gap-3 xl:gap-4">
          <div className="hidden flex-col items-end xl:flex">
            <span className="text-[10px] uppercase text-on-surface-variant">{t('top_owner')}</span>
            <span className="text-xs text-tertiary">{viewerLabel}</span>
          </div>
          <div className="h-10 px-3 bg-surface-container-highest border border-primary/20 flex items-center gap-2 rounded-tr-xl xl:px-4 xl:gap-3">
            <span className="text-xs uppercase text-on-surface-variant">{t('top_credits')}</span>
            <span className="font-bold text-lg text-secondary">⚡ {formatBalance(account?.balance)}</span>
            <Link to="/billing" className="ml-1 px-3 py-1 bg-secondary text-white text-[10px] font-bold uppercase hover:bg-secondary/80 transition-colors shadow-[0_0_10px_rgba(255,0,255,0.3)] xl:ml-2">
              {t('top_ledger')}
            </Link>
          </div>
        </div>

        <button
          className="relative flex h-10 w-10 items-center justify-center border border-primary/20 text-primary transition-colors hover:bg-primary/10"
          type="button"
          onClick={openDrawer}
          title={t('top_tasks')}
        >
          <ListTodo size={16} />
          {activeCount > 0 ? (
            <span className="absolute -right-1 -top-1 min-w-[18px] rounded-full bg-secondary px-1.5 py-0.5 text-[9px] font-bold text-white">
              {activeCount}
            </span>
          ) : null}
        </button>

        <button
          className="relative flex h-10 w-10 items-center justify-center border border-primary/20 text-primary transition-colors hover:bg-primary/10"
          type="button"
          onClick={openAnnouncement}
          title={t('top_announcement')}
        >
          <Bell size={16} />
          {siteSettings?.announcement.enabled ? <span className="absolute right-2 top-2 h-2 w-2 rounded-full bg-secondary" /> : null}
        </button>

        <a
          className="hidden h-10 items-center border border-primary/30 px-4 text-[10px] font-bold uppercase tracking-widest text-primary transition-colors hover:bg-primary/10 lg:flex"
          href={rechargeUrl}
          rel="noreferrer"
          target="_blank"
        >
          {t('top_recharge')}
        </a>

        {viewer?.authenticated ? (
          <button
            className="hidden h-10 px-4 border border-secondary/40 text-secondary text-[10px] uppercase tracking-widest hover:bg-secondary/10 transition-colors disabled:cursor-not-allowed disabled:opacity-60 lg:flex items-center gap-2"
            type="button"
            onClick={handleLogout}
            disabled={loggingOut}
          >
            {loggingOut ? <Loader2 className="animate-spin" size={14} /> : <LogOut size={14} />}
            {t('top_logout')}
          </button>
        ) : (
          <div className="hidden items-center gap-2 lg:flex">
            <Link className="h-10 px-4 border border-primary/30 text-primary text-[10px] uppercase tracking-widest hover:bg-primary/10 transition-colors flex items-center" to="/login">
              {t('top_login')}
            </Link>
            <Link className="h-10 px-4 bg-secondary text-white text-[10px] font-bold uppercase tracking-widest hover:bg-white hover:text-black transition-colors flex items-center" to="/register">
              {t('top_register')}
            </Link>
          </div>
        )}

        <Link to="/config" className="cursor-pointer hover:border-primary transition-colors shadow-[0_0_8px_rgba(255,0,255,0.2)]">
          <AvatarBadge
            className="w-10 h-10 rounded-full border-2"
            textClassName="text-xs"
            name={viewer?.user?.username}
            email={viewer?.user?.email}
            guestId={viewer?.guest_id}
          />
        </Link>
      </div>
      {mobileMenuOpen ? (
        <div className="fixed inset-x-0 top-16 z-[99] border-b border-primary/25 bg-surface/95 px-4 py-4 shadow-[0_18px_30px_rgba(0,0,0,0.45)] backdrop-blur-md lg:hidden">
          <div className="mb-4 border border-primary/15 bg-primary/5 p-3">
            <div className="text-[10px] uppercase tracking-widest text-on-surface-variant">{t('top_owner')}</div>
            <div className="mt-1 truncate text-xs text-tertiary">{viewerLabel}</div>
            <div className="mt-3 flex items-center justify-between gap-3 border-t border-white/10 pt-3">
              <span className="text-[10px] uppercase tracking-widest text-on-surface-variant">{t('top_credits')}</span>
              <span className="text-sm font-bold text-secondary">⚡ {formatBalance(account?.balance)}</span>
            </div>
          </div>

          <nav className="grid grid-cols-1 gap-2">
            {mobileNavItems.map((item) => {
              const isActive = location.pathname === item.path;
              return (
                <Link
                  key={item.path}
                  to={item.path}
                  className={`flex h-12 items-center gap-3 border px-4 text-xs uppercase tracking-widest transition-colors ${
                    isActive
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-white/10 bg-white/5 text-on-surface-variant hover:border-secondary hover:text-secondary'
                  }`}
                >
                  <item.icon size={17} />
                  {item.name}
                </Link>
              );
            })}
          </nav>

          <div className="mt-4 grid grid-cols-2 gap-2">
            <a
              className="flex h-11 items-center justify-center border border-secondary/35 bg-secondary/10 text-[10px] font-bold uppercase tracking-widest text-secondary transition-colors hover:bg-secondary/20"
              href={rechargeUrl}
              rel="noreferrer"
              target="_blank"
            >
              {t('top_recharge')}
            </a>
            {viewer?.authenticated ? (
              <button
                className="flex h-11 items-center justify-center gap-2 border border-secondary/35 text-[10px] uppercase tracking-widest text-secondary transition-colors hover:bg-secondary/10 disabled:cursor-not-allowed disabled:opacity-60"
                type="button"
                onClick={handleLogout}
                disabled={loggingOut}
              >
                {loggingOut ? <Loader2 className="animate-spin" size={14} /> : <LogOut size={14} />}
                {t('top_logout')}
              </button>
            ) : (
              <Link
                className="flex h-11 items-center justify-center border border-primary/30 text-[10px] uppercase tracking-widest text-primary transition-colors hover:bg-primary/10"
                to="/login"
              >
                {t('top_login')}
              </Link>
            )}
          </div>

          {!viewer?.authenticated ? (
            <Link
              className="mt-2 flex h-11 items-center justify-center bg-secondary text-[10px] font-bold uppercase tracking-widest text-white transition-colors hover:bg-white hover:text-black"
              to="/register"
            >
              {t('top_register')}
            </Link>
          ) : null}
        </div>
      ) : null}
    </header>
  );
}
