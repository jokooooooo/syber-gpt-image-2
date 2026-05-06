import { FormEvent, ReactNode, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Loader2, LockKeyhole, ShieldCheck } from 'lucide-react';
import { PublicAuthSettings, getAuthPublicSettings, loginAccount, loginAccount2FA } from '../api';
import { useAuth } from '../auth';
import { useNotifier } from '../notifications';
import { useSite } from '../site';

export default function Login() {
  const navigate = useNavigate();
  const { viewer, setViewer } = useAuth();
  const { t } = useSite();
  const { notifyError } = useNotifier();
  const [settings, setSettings] = useState<PublicAuthSettings | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [tempToken, setTempToken] = useState('');
  const [totpCode, setTotpCode] = useState('');
  const [maskedEmail, setMaskedEmail] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getAuthPublicSettings().then(setSettings).catch(() => setSettings(null));
  }, []);

  useEffect(() => {
    if (viewer?.authenticated) {
      navigate('/account', { replace: true });
    }
  }, [viewer, navigate]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    try {
      if (tempToken) {
        const result = await loginAccount2FA({ temp_token: tempToken, totp_code: totpCode.trim() });
        if (result.viewer) {
          setViewer(result.viewer);
          navigate('/account', { replace: true });
        }
        return;
      }

      const result = await loginAccount({
        email: email.trim(),
        password,
      });
      if (result.requires_2fa && result.temp_token) {
        setTempToken(result.temp_token);
        setMaskedEmail(result.user_email_masked || email.trim());
        return;
      }
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
    <div className="px-6 py-24 max-w-[640px] mx-auto min-h-screen flex items-center">
      <section className="w-full border border-primary/25 bg-black/70 p-8 md:p-10 shadow-[0_0_40px_rgba(0,243,255,0.08)]">
        <div className="text-[10px] text-secondary uppercase tracking-widest font-bold mb-3">
          joko-image2 {t('login_access')}
        </div>
        <h1 className="text-3xl md:text-4xl font-black text-white mb-3 uppercase">
          {tempToken ? t('login_title_2fa') : t('login_title')}
        </h1>
        <p className="text-sm text-white/50 mb-8">
          {tempToken
            ? t('login_desc_2fa', { value: maskedEmail })
            : t('login_desc')}
        </p>

        <form className="space-y-5" onSubmit={handleSubmit}>
          {!tempToken && (
            <>
              <Field label={t('login_email')}>
                <input className="input-cyber" type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
              </Field>
              <Field label={t('login_password')}>
                <input className="input-cyber" type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
              </Field>
            </>
          )}

          {tempToken && (
            <Field label={t('login_totp')}>
              <input
                className="input-cyber tracking-[0.35em] text-center"
                inputMode="numeric"
                maxLength={6}
                value={totpCode}
                onChange={(event) => setTotpCode(event.target.value.replace(/\D/g, '').slice(0, 6))}
              />
            </Field>
          )}

          <button
            className="w-full bg-secondary text-white font-bold px-6 py-3 uppercase tracking-widest hover:bg-white hover:text-black transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
            disabled={loading}
            type="submit"
          >
            {loading ? <Loader2 className="animate-spin" size={16} /> : tempToken ? <ShieldCheck size={16} /> : <LockKeyhole size={16} />}
            {tempToken ? t('login_submit_2fa') : t('login_submit')}
          </button>
        </form>

        <div className="mt-6 pt-6 border-t border-white/10 text-xs text-white/50 flex items-center justify-between gap-4">
          <span>{t('login_new')}</span>
          <Link className="text-primary uppercase tracking-widest hover:text-secondary" to="/register">
            {t('top_register')}
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
