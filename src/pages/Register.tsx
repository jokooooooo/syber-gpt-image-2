import { FormEvent, ReactNode, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Loader2, MailPlus, Send } from 'lucide-react';
import { PublicAuthSettings, getAuthPublicSettings, registerAccount, sendVerifyCode } from '../api';
import { useAuth } from '../auth';
import { useNotifier } from '../notifications';
import { useSite } from '../site';

export default function Register() {
  const navigate = useNavigate();
  const { viewer, setViewer } = useAuth();
  const { t } = useSite();
  const { notifyError, notifySuccess } = useNotifier();
  const [settings, setSettings] = useState<PublicAuthSettings | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [verifyCode, setVerifyCode] = useState('');
  const [promoCode, setPromoCode] = useState('');
  const [invitationCode, setInvitationCode] = useState('');
  const [countdown, setCountdown] = useState(0);
  const [loading, setLoading] = useState(false);
  const [sendingCode, setSendingCode] = useState(false);

  useEffect(() => {
    getAuthPublicSettings().then(setSettings).catch(() => setSettings(null));
  }, []);

  useEffect(() => {
    if (viewer?.authenticated) {
      navigate('/account', { replace: true });
    }
  }, [viewer, navigate]);

  useEffect(() => {
    if (!countdown) return;
    const timer = window.setTimeout(() => setCountdown((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearTimeout(timer);
  }, [countdown]);

  const canRegister = useMemo(() => settings?.registration_enabled !== false && settings?.backend_mode_enabled !== true, [settings]);

  async function handleSendCode() {
    setSendingCode(true);
    try {
      const result = await sendVerifyCode({ email: email.trim() });
      notifySuccess(result.message);
      setCountdown(result.countdown || 60);
    } catch (err) {
      notifyError(err);
    } finally {
      setSendingCode(false);
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    try {
      const result = await registerAccount({
        email: email.trim(),
        password,
        verify_code: settings?.email_verify_enabled ? verifyCode.trim() : undefined,
        promo_code: settings?.promo_code_enabled ? promoCode.trim() || undefined : undefined,
        invitation_code: settings?.invitation_code_enabled ? invitationCode.trim() || undefined : undefined,
      });
      if (result.viewer) {
        setViewer(result.viewer);
        navigate('/account', { replace: true });
      }
    } catch (err) {
      notifyError(err);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="px-6 py-24 max-w-[760px] mx-auto min-h-screen flex items-center">
      <section className="w-full border border-primary/25 bg-black/70 p-8 md:p-10 shadow-[0_0_40px_rgba(255,0,255,0.08)]">
        <div className="text-[10px] text-secondary uppercase tracking-widest font-bold mb-3">
          joko-image2 {t('register_access')}
        </div>
        <h1 className="text-3xl md:text-4xl font-black text-white mb-3 uppercase">{t('register_title')}</h1>
        <p className="text-sm text-white/50 mb-8">
          {t('register_desc')}
        </p>

        {!canRegister && (
          <div className="border border-error/40 bg-error/10 p-4 text-xs text-error">
            {t('register_disabled')}
          </div>
        )}

        <form className="space-y-5 mt-6" onSubmit={handleSubmit}>
          <Field label={t('register_email')}>
            <input className="input-cyber" type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
          </Field>

          <Field label={t('register_password')}>
            <input className="input-cyber" type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
          </Field>

          {settings?.email_verify_enabled && (
            <div className="grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3 items-end">
              <Field label={t('register_verify_code')}>
                <input className="input-cyber" value={verifyCode} onChange={(event) => setVerifyCode(event.target.value)} />
              </Field>
              <button
                className="h-[46px] px-5 border border-primary/30 text-primary uppercase tracking-widest text-xs hover:bg-primary/10 disabled:opacity-50 flex items-center justify-center gap-2"
                disabled={sendingCode || !email.trim() || countdown > 0}
                type="button"
                onClick={handleSendCode}
              >
                {sendingCode ? <Loader2 className="animate-spin" size={14} /> : <Send size={14} />}
                {countdown > 0 ? `${countdown}s` : t('register_send_code')}
              </button>
            </div>
          )}

          {settings?.promo_code_enabled && (
            <Field label={t('register_promo')}>
              <input className="input-cyber" value={promoCode} onChange={(event) => setPromoCode(event.target.value)} />
            </Field>
          )}

          {settings?.invitation_code_enabled && (
            <Field label={t('register_invitation')}>
              <input className="input-cyber" value={invitationCode} onChange={(event) => setInvitationCode(event.target.value)} />
            </Field>
          )}

          <button
            className="w-full bg-secondary text-white font-bold px-6 py-3 uppercase tracking-widest hover:bg-white hover:text-black transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
            disabled={loading || !canRegister}
            type="submit"
          >
            {loading ? <Loader2 className="animate-spin" size={16} /> : <MailPlus size={16} />}
            {t('register_submit')}
          </button>
        </form>

        <div className="mt-6 pt-6 border-t border-white/10 text-xs text-white/50 flex items-center justify-between gap-4">
          <span>{t('register_exists')}</span>
          <Link className="text-primary uppercase tracking-widest hover:text-secondary" to="/login">
            {t('login_submit')}
          </Link>
        </div>
      </section>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <label className="text-[10px] text-secondary uppercase tracking-widest font-bold">{label}</label>
      {children}
    </div>
  );
}
