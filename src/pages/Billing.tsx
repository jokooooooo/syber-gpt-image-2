import { useEffect, useState } from 'react';
import { Activity, ArrowRight, CreditCard } from 'lucide-react';
import { BalanceInfo, LedgerEntry, formatBalance, formatDate, getBalance, getLedger } from '../api';
import { useAuth } from '../auth';
import { useNotifier } from '../notifications';
import { useSite } from '../site';

export default function Billing() {
  const { viewer } = useAuth();
  const { t } = useSite();
  const { notifyError } = useNotifier();
  const rechargeUrl = 'https://ai.get-money.locker';
  const [balance, setBalance] = useState<BalanceInfo>();
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);

  useEffect(() => {
    Promise.all([getBalance(), getLedger(50)])
      .then(([balanceData, ledgerData]) => {
        setBalance(balanceData);
        setLedger(ledgerData.items);
      })
      .catch(notifyError);
  }, [viewer?.owner_id, notifyError]);

  return (
    <div className="md:ml-64 px-6 md:px-12 py-8 max-w-[1440px] mx-auto min-h-screen pt-24 pb-12 bg-[radial-gradient(ellipse_at_top,var(--color-surface-container-high),var(--color-background))] font-mono">
      <div className="flex flex-col gap-2 mb-10 border-b border-white/10 pb-6">
        <div className="flex items-center gap-2 text-[10px] text-primary uppercase font-bold tracking-widest">
          <span className="w-4 h-[1px] bg-primary"></span> {t('billing_tag')}
        </div>
        <h1 className="text-4xl md:text-5xl text-on-surface font-bold tracking-tighter">{t('billing_title')}</h1>
      </div>

      <section className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
        <div className="lg:col-span-1 bg-black border border-secondary/30 p-6">
          <div className="flex items-center gap-2 text-secondary text-[10px] uppercase tracking-widest mb-6">
            <CreditCard size={16} /> {t('billing_remaining')}
          </div>
          <div className="text-5xl text-white font-black tracking-tighter">{formatBalance(balance)}</div>
          <div className="mt-4 text-xs text-white/40">{balance?.ok ? t('billing_synced') : balance?.message || t('billing_not_configured')}</div>
          <div className="mt-6 border-t border-white/10 pt-5">
            <div className="mb-3 text-xs text-white/50">{t('billing_recharge_desc')}</div>
            <a
              className="inline-flex items-center gap-2 border border-primary/30 px-5 py-3 text-xs font-bold uppercase tracking-widest text-primary transition-colors hover:bg-primary/10"
              href={rechargeUrl}
              rel="noreferrer"
              target="_blank"
            >
              {t('billing_recharge')}
              <ArrowRight size={14} />
            </a>
          </div>
        </div>

        <div className="lg:col-span-2 bg-black border border-primary/20 p-6">
          <div className="flex items-center gap-2 text-primary text-[10px] uppercase tracking-widest mb-6">
            <Activity size={16} /> {t('billing_local_ledger')}
          </div>
          <div className="divide-y divide-white/5">
            {ledger.length === 0 && <div className="py-8 text-white/40 text-xs uppercase">{t('billing_empty')}</div>}
            {ledger.map((item) => (
              <div key={item.id} className="py-4 flex items-center justify-between gap-4 text-xs">
                <div>
                  <div className="flex flex-wrap items-center gap-2 text-white">
                    <span>{item.description}</span>
                    {isActualLedger(item) && (
                      <span className="border border-secondary/40 px-2 py-0.5 text-[9px] uppercase tracking-widest text-secondary">
                        {t('billing_actual')}
                      </span>
                    )}
                    {isEstimatedLedger(item) && (
                      <span className="border border-tertiary/40 px-2 py-0.5 text-[9px] uppercase tracking-widest text-tertiary">
                        {t('billing_estimated')}
                      </span>
                    )}
                  </div>
                  <div className="text-white/40 mt-1">{formatDate(item.created_at)}</div>
                </div>
                <div className="text-secondary flex items-center gap-2">
                  {item.amount.toFixed(4)} {item.currency}
                  <ArrowRight size={12} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}

function isEstimatedLedger(item: LedgerEntry) {
  const source = String(item.metadata?.cost_source || '');
  return source.startsWith('local_image_price');
}

function isActualLedger(item: LedgerEntry) {
  return item.metadata?.cost_source === 'sub2api_actual_cost';
}
