import { useEffect, useState } from 'react';
import { Activity, Database, Server, UserCircle } from 'lucide-react';
import { AccountInfo, formatBalance, formatDate, getAccount } from '../api';
import { useAuth } from '../auth';
import AvatarBadge from '../components/AvatarBadge';
import { useNotifier } from '../notifications';
import { useSite } from '../site';

export default function Account() {
  const { viewer } = useAuth();
  const { t } = useSite();
  const { notifyError } = useNotifier();
  const [account, setAccount] = useState<AccountInfo | null>(null);

  useEffect(() => {
    getAccount().then(setAccount).catch(notifyError);
  }, [viewer?.owner_id, notifyError]);

  return (
    <div className="md:ml-64 px-6 md:px-12 py-8 max-w-[1440px] mx-auto min-h-screen pt-24 pb-12 bg-[radial-gradient(ellipse_at_top,var(--color-surface-container-high),var(--color-background))] font-mono">
      <div className="flex flex-col gap-2 mb-10 border-b border-white/10 pb-6">
        <div className="flex items-center gap-2 text-[10px] text-secondary uppercase font-bold tracking-widest">
          <span className="w-4 h-[1px] bg-secondary"></span> {t('account_tag')}
        </div>
        <h1 className="text-4xl md:text-5xl text-on-surface font-bold tracking-tighter">{t('account_title')}</h1>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <section className="lg:col-span-2 bg-black border border-primary/20 p-6">
          <h2 className="text-primary mb-6 uppercase flex items-center gap-2 font-bold tracking-wider text-xs">
            <UserCircle size={18} /> {t('account_identity')}
          </h2>
          <div className="mb-6 flex items-center gap-4">
            <AvatarBadge
              className="w-16 h-16"
              textClassName="text-lg"
              name={account?.user.username || account?.user.name}
              email={account?.user.email}
              guestId={account?.viewer.guest_id}
            />
            <div className="min-w-0">
              <div className="text-lg text-white font-bold truncate">{account?.user.username || account?.user.name || t('account_guest')}</div>
              <div className="text-xs text-white/45 break-all">{account?.user.email || account?.viewer.owner_id || '--'}</div>
            </div>
          </div>
          <div className="space-y-4 text-sm">
            <Row label={t('account_owner')} value={account?.user.authenticated ? t('account_registered') : t('account_guest')} />
            <Row label={t('account_user')} value={account?.user.name || '--'} />
            <Row label={t('account_sub2api_username')} value={account?.user.username || '--'} />
            <Row label={t('account_email')} value={account?.user.email || '--'} />
            <Row label={t('account_model')} value={account?.user.model || 'gpt-image-2'} />
            <Row
              label={t('account_api_key')}
              value={account?.user.api_key_set
                ? account?.user.api_key_source === 'managed'
                  ? t('account_api_managed')
                  : account?.user.api_key_source === 'manual_override'
                    ? t('account_api_override')
                    : t('account_api_manual')
                : t('account_api_missing')}
            />
          </div>
        </section>

        <Metric icon={Activity} label={t('account_balance')} value={formatBalance(account?.balance)} sub={account?.balance.ok ? 'JokoAI /v1/usage' : account?.balance.message || 'Not connected'} />
        <Metric icon={Database} label={t('account_history')} value={String(account?.stats.total ?? 0)} sub={t('account_succeeded', { value: account?.stats.succeeded ?? 0 })} />
        <Metric icon={Server} label={t('account_edits')} value={String(account?.stats.edits ?? 0)} sub={t('account_last', { value: formatDate(account?.stats.last_generation_at) })} />
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1 border-b border-white/5 pb-3">
      <span className="text-[10px] text-white/40 uppercase tracking-widest">{label}</span>
      <span className="text-primary break-all">{value}</span>
    </div>
  );
}

function Metric({ icon: Icon, label, value, sub }: { icon: typeof Activity; label: string; value: string; sub: string }) {
  return (
    <section className="bg-black border border-white/10 p-6 min-h-40">
      <div className="flex items-center gap-2 text-secondary text-[10px] uppercase tracking-widest mb-5">
        <Icon size={16} /> {label}
      </div>
      <div className="text-4xl text-white font-black tracking-tighter">{value}</div>
      <div className="mt-3 text-[10px] text-white/40 uppercase">{sub}</div>
    </section>
  );
}
