/**
 * Acceso al panel: SOLO usuario y contraseña. El PIN se queda en la
 * operación diaria (ámbito pos); administrar exige credencial completa.
 */

import { useState, type FormEvent } from 'react';
import { LogIn } from 'lucide-react';
import { useSession } from '../state/session';
import { Banner } from './ui';

export default function Login() {
  const loginWithPassword = useSession((state) => state.loginWithPassword);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await loginWithPassword(username.trim(), password);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-dvh place-items-center p-4">
      <form onSubmit={submit} className="card w-full max-w-sm">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-xl bg-indigo-600 text-white">
            <LogIn size={22} aria-hidden />
          </div>
          <h1 className="text-lg font-bold text-slate-800">TPV · Administración</h1>
          <p className="mt-1 text-sm text-slate-500">Acceso con usuario y contraseña</p>
        </div>

        {error && <Banner>{error}</Banner>}

        <label className="block">
          <span className="label-base">Usuario</span>
          <input
            className="input-base"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoFocus
            required
            minLength={2}
          />
        </label>
        <label className="mt-3 block">
          <span className="label-base">Contraseña</span>
          <input
            className="input-base"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        <button type="submit" className="btn-primary mt-5 w-full" disabled={busy || !username.trim() || !password}>
          {busy ? 'Entrando…' : 'Entrar'}
        </button>
      </form>
    </div>
  );
}
