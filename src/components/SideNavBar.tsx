import { Link, useLocation } from 'react-router-dom';
import { CreditCard, Heart, History, Terminal, UserCircle, Zap } from 'lucide-react';
import { useAuth } from '../auth';
import { useSite } from '../site';

export default function SideNavBar() {
  const location = useLocation();
  const { viewer } = useAuth();
  const { t } = useSite();

  const navItems = [
    { name: t('side_history'), path: '/history', icon: History },
    ...(viewer?.authenticated ? [{ name: t('side_favorites'), path: '/favorites', icon: Heart }] : []),
    { name: t('side_account'), path: '/account', icon: UserCircle },
    { name: t('side_config'), path: '/config', icon: Terminal },
    { name: t('side_billing'), path: '/billing', icon: CreditCard },
  ];

  return (
    <aside className="fixed left-0 top-16 h-[calc(100vh-64px)] w-64 z-40 bg-surface border-r border-primary/20 shadow-none pt-8 flex-col hidden md:flex shrink-0 font-mono">
      <div className="px-6 mb-8 flex flex-col gap-1 items-start">
        <h2 className="text-primary font-bold tracking-tighter text-lg uppercase">{t('side_title')}</h2>
        <div className="px-2 py-0.5 bg-primary/10 border border-primary text-primary text-[10px]">
          V2.0.4-STABLE
        </div>
      </div>
      
      <nav className="flex-1 flex flex-col gap-2">
        {navItems.map((item) => {
          const isActive = location.pathname === item.path;
          return (
            <Link
              key={item.name}
              to={item.path}
              className={`flex items-center gap-3 px-4 py-3 text-xs uppercase tracking-widest transition-all duration-150 ${
                isActive 
                  ? 'bg-primary/10 text-primary border-l-[3px] border-primary' 
                  : 'text-on-surface-variant hover:bg-white/5 hover:text-secondary'
              }`}
            >
              <item.icon size={18} />
              {item.name}
            </Link>
          );
        })}
      </nav>

      <div className="p-6 border-t border-primary/20 mt-auto">
        <Link to="/" className="w-full flex items-center justify-center gap-2 bg-secondary/10 text-secondary border border-secondary py-3 text-xs uppercase font-bold hover:bg-secondary/20 transition-colors shadow-[0_0_10px_rgba(255,0,255,0.2)]">
          <Zap size={16} />
          {t('side_generate')}
        </Link>
      </div>
    </aside>
  );
}
