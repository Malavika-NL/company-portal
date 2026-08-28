import { FormEvent, useEffect, useState } from 'react';
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Building2,
  Check,
  ChevronRight,
  CircleCheck,
  Headphones,
  LockKeyhole,
  LogOut,
  Mail,
  Rocket,
  Send,
  ShieldCheck,
  Sparkles,
  BriefcaseBusiness,
  UsersRound,
} from 'lucide-react';
import { api, Company, Session, session } from './api';

type Application = { key: string; name: string; launch_url: string; entitled?: boolean };
type TileState = 'entitled' | 'not_entitled' | 'not_provisioned' | 'unreachable' | 'checking';
const DOWNSTREAM_STATUSES: TileState[] = ['entitled', 'not_entitled', 'not_provisioned', 'unreachable'];

const applicationDetails: Record<string, { description: string; label: string }> = {
  marketing_crm: {
    description: 'Plan campaigns, manage audiences, and track marketing performance.',
    label: 'Campaign workspace',
  },
  salespie: {
    description: 'Manage accounts, opportunities, targets, and the complete sales pipeline.',
    label: 'Sales workspace',
  },
  bdcrm: {
    description: 'Organize business development, contacts, activities, and follow-ups.',
    label: 'Business development',
  },
};

const companyInitials = (name: string) => name
  .split(/\s+/)
  .filter(Boolean)
  .slice(0, 2)
  .map((part) => part[0])
  .join('')
  .toUpperCase();

export default function App() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [apps, setApps] = useState<Application[]>([]);
  const [selectedCompany, setSelectedCompany] = useState<Company | null>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [launchingApp, setLaunchingApp] = useState<string | null>(null);
  const [active, setActive] = useState<Session | null>(() => {
    // A manual visit to the Portal starts a new sign-in flow.  CRM logout
    // redirects include ?workspace=1, which is the only route allowed to
    // restore the portal session and show the CRM buttons again.
    if (new URLSearchParams(window.location.search).get('workspace') !== '1') {
      session.clear();
      return null;
    }
    return session.get();
  });
  const [tileStatus, setTileStatus] = useState<Record<string, TileState>>({});

  useEffect(() => {
    let cancelled = false;

    const loadCompanies = async () => {
      for (let attempt = 0; attempt < 60 && !cancelled; attempt += 1) {
        try {
          const response = await fetch('/api/companies/', { cache: 'no-store' });
          if (!response.ok) throw new Error('Portal backend is starting.');
          const data = await response.json();
          if (!cancelled) {
            setCompanies(data);
            setError('');
          }
          return;
        } catch {
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
        }
      }
      if (!cancelled) setError('Company Portal backend could not be started.');
    };

    void loadCompanies();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!active?.company) return;
    api('/api/portal/workspace/')
      .then(async (response) => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          session.clear();
          setApps([]);
          setError(response.status === 401
            ? 'Your secure portal session expired. Please choose your company and sign in again.'
            : data.detail || 'Your portal session could not be verified. Please sign in again.');
          return;
        }
        setApps(data.applications || []);
      })
      .catch(() => {
        session.clear();
        setApps([]);
        setError('Your portal session could not be verified. Please sign in again.');
      });
  }, [active?.company?.code]);

  useEffect(() => {
    if (!apps.length) return;
    const links: HTMLLinkElement[] = [];

    for (const app of apps) {
      const origin = new URL(app.launch_url, window.location.href).origin;
      const preconnect = document.createElement('link');
      preconnect.rel = 'preconnect';
      preconnect.href = origin;
      preconnect.crossOrigin = 'anonymous';
      document.head.appendChild(preconnect);
      links.push(preconnect);

      // The local CRM frontends run through Vite. Priming their entry modules
      // while the user is choosing a card removes first-click compilation lag.
      const isLocalVite = ['localhost', '127.0.0.1'].includes(new URL(origin).hostname);
      for (const modulePath of isLocalVite
        ? ['/src/main.tsx', ...(app.key === 'salespie' ? ['/src/App.tsx'] : [])]
        : []) {
        const preload = document.createElement('link');
        preload.rel = 'modulepreload';
        preload.href = `${origin}${modulePath}`;
        preload.crossOrigin = 'anonymous';
        document.head.appendChild(preload);
        links.push(preload);
      }
    }

    return () => links.forEach((link) => link.remove());
  }, [apps]);

  useEffect(() => {
    if (!apps.length) { setTileStatus({}); return; }
    setTileStatus(Object.fromEntries(
      apps.map((app) => [
        app.key,
        app.entitled === false ? 'not_entitled' : app.key === 'marketing_crm' ? 'entitled' : 'checking',
      ]),
    ));

    let cancelled = false;
    api('/api/portal/sso/preflight/')
      .then(async (response) => {
        const data = await response.json().catch(() => ({}));
        if (cancelled || !data.applications) return;
        setTileStatus((current) => {
          const next = { ...current };
          for (const [key, info] of Object.entries<{ status: string }>(data.applications)) {
            next[key] = DOWNSTREAM_STATUSES.includes(info.status as TileState)
              ? (info.status as TileState)
              : 'unreachable';
          }
          return next;
        });
      })
      .catch(() => {
        if (cancelled) return;
        setTileStatus((current) => {
          const next = { ...current };
          for (const app of apps) {
            if (app.key !== 'marketing_crm' && app.entitled !== false) next[app.key] = 'unreachable';
          }
          return next;
        });
      });

    return () => { cancelled = true; };
  }, [apps]);

  const openLogin = (company: Company) => {
    setError('');
    setPassword('');
    setSelectedCompany(company);
  };

  const login = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedCompany) return;
    setLoading(true);
    setError('');
    try {
      const response = await fetch('/api/auth/marketing/login/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ company_code: selectedCompany.code, email, password }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Unable to sign in.');
      session.set(data);
      setActive(data);
      setPassword('');
      setSelectedCompany(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to sign in.');
    } finally {
      setLoading(false);
    }
  };

  const launch = async (application: string) => {
    if (launchingApp) return;
    setLaunchingApp(application);
    setError('');
    try {
      const response = await api('/api/portal/sso/launch/', {
        method: 'POST',
        body: JSON.stringify({ application }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.launch_url) {
        throw new Error(data.code === 'ACCOUNT_NOT_LINKED'
          ? 'No access — contact your Marketing CRM administrator.'
          : data.detail || 'Unable to open CRM.');
      }
      window.location.assign(data.launch_url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to open CRM.');
      setLaunchingApp(null);
    }
  };

  const badgeLabel = (key: string) => {
    const state = tileStatus[key];
    if (state === 'entitled') return 'Connected';
    if (state === 'not_entitled') return 'No access';
    if (state === 'not_provisioned') return 'Setup pending';
    if (state === 'unreachable') return 'Unavailable';
    return 'Checking…';
  };

  const appIcon = (key: string) => key === 'marketing_crm'
    ? <Send aria-hidden="true" />
    : key === 'salespie'
      ? <Rocket aria-hidden="true" />
      : <UsersRound aria-hidden="true" />;

  const signOut = () => {
    session.clear();
    setActive(null);
    setApps([]);
    setSelectedCompany(null);
    setEmail('');
    setPassword('');
    setError('');
    setLaunchingApp(null);
  };

  return (
    <div className="portal-shell">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />

      <header className="portal-header">
        <div className="header-inner">
          <div className="brand-lockup">
            <span className="brand-mark"><Building2 size={21} aria-hidden="true" /></span>
            <span className="brand-copy">
              <strong>Company Portal</strong>
              <small>Unified CRM access</small>
            </span>
          </div>

          <div className="header-actions">
            {active?.company ? (
              <>
                <span className="company-chip">
                  <span className="status-dot" />
                  {active.company.name}
                </span>
                <button className="signout-button" onClick={signOut}>
                  <LogOut size={16} aria-hidden="true" />
                  <span>Sign out</span>
                </button>
              </>
            ) : (
              <><span className="header-status"><i /> Systems operational</span><span className="secure-chip"><ShieldCheck size={16} /> Secure access</span></>
            )}
          </div>
        </div>
      </header>

      <main className="portal-main">
        {error && (
          <div className="error-banner" role="alert" aria-live="polite">
            <span className="error-icon">!</span>
            <span>{error}</span>
          </div>
        )}

        {active?.company ? (
          <section className="workspace-view">
            <div className="workspace-heading">
              <div>
                <p className="eyebrow"><Sparkles size={14} /> Connected workspace</p>
                <h1>Choose where you want to work</h1>
                <p className="lead-copy">
                  Your secure session is ready. Open any connected CRM without signing in again.
                </p>
              </div>
              <div className="connection-card">
                <CircleCheck size={22} />
                <div><strong>Single sign-on active</strong><span>All connections are protected</span></div>
              </div>
            </div>

            <div className="app-grid">
              {apps.length === 0 && [1, 2, 3].map((item) => <div className="app-card skeleton" key={item} />)}
              {apps.map((app) => {
                const details = applicationDetails[app.key] || {
                  description: 'Open your connected business workspace.',
                  label: 'Connected application',
                };
                const muted = app.entitled === false;
                return (
                  <button
                    className={`app-card app-${app.key}${muted ? ' app-card-muted' : ''}`}
                    key={app.key}
                    disabled={muted || !!launchingApp}
                    onClick={() => { if (!muted) launch(app.key); }}
                  >
                    <div className="app-card-top">
                      <span className="app-icon">{appIcon(app.key)}</span>
                      <span className="connected-badge"><Check size={12} /> {badgeLabel(app.key)}</span>
                    </div>
                    <span className="app-label">{details.label}</span>
                    <h2>{app.name}</h2>
                    <p>{details.description}</p>
                    <span className="open-action">
                      {muted ? 'Access not enabled' : launchingApp === app.key ? 'Opening workspace...' : 'Open workspace'}
                      {!muted && launchingApp !== app.key && <ArrowRight size={17} />}
                    </span>
                  </button>
                );
              })}
            </div>

            <div className="workspace-footer">
              <ShieldCheck size={16} />
              Your Marketing CRM identity is securely shared with each connected workspace.
            </div>
          </section>
        ) : selectedCompany ? (
          <section className="auth-layout">
            <div className="auth-context">
              <button className="back-button" onClick={() => { setError(''); setSelectedCompany(null); }}>
                <ArrowLeft size={16} /> Change company
              </button>
              <div className="company-identity">
                <span className="company-monogram">{companyInitials(selectedCompany.name)}</span>
                <div><small>Signing in to</small><strong>{selectedCompany.name}</strong></div>
              </div>
              <h1>One login.<br />Every CRM.</h1>
              <p>Use your Marketing CRM account once to access every connected business workspace.</p>
              <ul className="benefit-list">
                <li><Check size={15} /> No repeated CRM login screens</li>
                <li><Check size={15} /> Existing roles and data stay unchanged</li>
                <li><Check size={15} /> Secure one-time application handoff</li>
              </ul>
            </div>

            <div className="login-panel">
              <div className="panel-heading">
                <div className="login-panel-topline">
                  <span className="login-crest"><ShieldCheck size={18} /></span>
                  <span className="step-label">Step 2 of 2</span>
                  <span className="verified-label"><i /> Encrypted</span>
                </div>
                <h2>Welcome back</h2>
                <p>Enter your Marketing CRM credentials to continue.</p>
              </div>
              <form onSubmit={login}>
                <label>
                  <span>Email address</span>
                  <span className="input-shell">
                    <Mail size={18} aria-hidden="true" />
                    <input
                      type="email"
                      autoComplete="email"
                      value={email}
                      onChange={(event) => setEmail(event.target.value)}
                      placeholder="name@company.com"
                      required
                    />
                  </span>
                </label>
                <label>
                  <span>Password</span>
                  <span className="input-shell">
                    <LockKeyhole size={18} aria-hidden="true" />
                    <input
                      type="password"
                      autoComplete="current-password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      placeholder="Enter your password"
                      required
                    />
                  </span>
                </label>
                <button className="primary-button" disabled={loading} type="submit">
                  {loading ? 'Signing you in...' : 'Continue securely'}
                  {!loading && <ArrowRight size={18} />}
                </button>
              </form>
              <p className="privacy-note"><ShieldCheck size={15} /> Your password is verified by Marketing CRM and is never stored here.</p>
              <a className="support-contact" href="mailto:support@nltechnologies.in">
                <span className="support-icon"><Headphones size={18} /></span>
                <span><strong>Need help signing in?</strong><small>Contact the NL Technologies support team</small></span>
                <ChevronRight size={17} aria-hidden="true" />
              </a>
            </div>
          </section>
        ) : (
          <section className="selection-layout">
            <div className="welcome-copy">
              <div className="hero-meta"><span>NL Technologies</span><i /> <span>Company access portal</span></div>
              <p className="eyebrow"><span className="eyebrow-spark"><Sparkles size={13} /></span> Unified workspace access</p>
              <h1>Every team.<br /><span>One secure</span> starting point.</h1>
              <p className="lead-copy">
                Select your organization, then use your Marketing CRM account once to move seamlessly between your business tools.
              </p>
              <div className="access-visual" aria-hidden="true">
                <div className="access-visual-top">
                  <span className="visual-live"><i /> LIVE CONNECTION</span>
                  <span>3 workspaces</span>
                </div>
                <div className="access-map">
                  <span className="map-pulse pulse-one" />
                  <span className="map-pulse pulse-two" />
                  <div className="map-hub"><ShieldCheck size={20} /><span>Secure<br />access</span></div>
                  <div className="map-line line-one" />
                  <div className="map-line line-two" />
                  <div className="map-line line-three" />
                  <span className="map-node node-marketing"><Send size={15} /> Marketing</span>
                  <span className="map-node node-sales"><BarChart3 size={15} /> Sales</span>
                  <span className="map-node node-bd"><BriefcaseBusiness size={15} /> Development</span>
                </div>
              </div>
              <div className="hero-assurance"><ShieldCheck size={16} /><span><strong>Protected identity</strong> Your access is verified before every workspace opens.</span></div>
              <div className="feature-row">
                <span><strong>1</strong><small>secure login</small></span>
                <i />
                <span><strong>3</strong><small>connected CRMs</small></span>
                <i />
                <span><Activity size={22} /><small>always connected</small></span>
              </div>
            </div>

            <div className="company-panel">
              <div className="panel-orbit orbit-one" aria-hidden="true" />
              <div className="panel-orbit orbit-two" aria-hidden="true" />
              <div className="panel-heading">
                <div className="panel-progress"><span>Step 1 of 2</span><i><b /></i></div>
                <h2>Select your company</h2>
                <p>Choose the organization you want to access. Your roles and permissions will follow you.</p>
              </div>
              <div className="company-panel-note"><span><ShieldCheck size={15} /></span><p>One verified identity gives you access to every authorized workspace.</p></div>
              <div className="company-list">
                {companies.length === 0 && !error && [1, 2].map((item) => <div className="company-option skeleton" key={item} />)}
                {companies.map((company) => (
                  <button className="company-option" key={company.code} onClick={() => openLogin(company)}>
                    <span className="company-option-top">
                      <span className="company-monogram">{companyInitials(company.name)}</span>
                      <span className="option-arrow"><ArrowRight size={18} /></span>
                    </span>
                    <span className="company-option-copy">
                      <small>Organization</small>
                      <strong>{company.name}</strong>
                      <span>Continue to secure login <ChevronRight size={14} /></span>
                    </span>
                  </button>
                ))}
              </div>
              <div className="panel-footer"><LockKeyhole size={14} /> Authorized company members only</div>
            </div>
          </section>
        )}
      </main>

      <footer className="portal-footer">
        <span>NL Technologies</span>
        <span>Secure company access portal</span>
      </footer>
    </div>
  );
}
