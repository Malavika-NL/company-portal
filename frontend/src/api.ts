export type Company = { id: number; code: string; name: string; role?: string };
export type Session = { access: string; refresh: string; company?: Company; user?: { id: number; email: string; name?: string } };
const key = 'company_portal_session';
export const session = { get: (): Session | null => { try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch { return null; } }, set: (v: Session) => localStorage.setItem(key, JSON.stringify(v)), clear: () => localStorage.removeItem(key) };

const refreshPortalSession = async (current: Session): Promise<Session | null> => {
  try {
    const response = await fetch('/api/auth/token/refresh/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh: current.refresh }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.access) return null;

    const updated = { ...current, access: data.access, refresh: data.refresh || current.refresh };
    session.set(updated);
    return updated;
  } catch {
    return null;
  }
};

export async function api(path: string, init: RequestInit = {}) {
  const request = (current: Session | null) => {
    const headers = new Headers(init.headers);
    headers.set('Content-Type', 'application/json');
    if (current?.access) headers.set('Authorization', `Bearer ${current.access}`);
    return fetch(path, { ...init, headers });
  };

  const current = session.get();
  const response = await request(current);
  if (response.status !== 401 || !current?.refresh || path === '/api/auth/token/refresh/') return response;

  const refreshed = await refreshPortalSession(current);
  return refreshed ? request(refreshed) : response;
}
