/**
 * Acceso del terminal: usuario y contraseña contra /auth/login. Mínimo para
 * poder usar el TPV; el ciclo completo de sesión llega con las fases de
 * terminales (PIN, refresco rotativo, cierre por inactividad).
 */

import { useState } from 'react';
import { LockKeyhole } from 'lucide-react';
import { useSession } from '../state/session';

export default function LoginView() {
  const login = useSession((state) => state.login);
  const checking = useSession((state) => state.checking);
  const error = useSession((state) => state.error);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    void login(username, password);
  };

  return (
    <div className="flex h-screen items-center justify-center bg-slate-900 p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-2xl bg-slate-800 p-8 shadow-xl"
      >
        <div className="mb-6 flex items-center gap-3">
          <LockKeyhole className="size-8 text-amber-400" aria-hidden />
          <h1 className="text-2xl font-bold text-slate-100">TPV</h1>
        </div>

        <label className="mb-1 block text-sm font-medium text-slate-300" htmlFor="login-user">
          Usuario
        </label>
        <input
          id="login-user"
          autoFocus
          autoComplete="username"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          className="mb-4 w-full rounded-lg border border-slate-600 bg-slate-900 px-4 py-3 text-lg text-slate-100 outline-none focus:border-amber-400"
        />

        <label className="mb-1 block text-sm font-medium text-slate-300" htmlFor="login-pass">
          Contraseña
        </label>
        <input
          id="login-pass"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="mb-4 w-full rounded-lg border border-slate-600 bg-slate-900 px-4 py-3 text-lg text-slate-100 outline-none focus:border-amber-400"
        />

        {error && (
          <p role="alert" className="mb-4 text-sm text-red-400">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={checking || !username || !password}
          className="w-full rounded-lg bg-amber-500 px-4 py-3 text-lg font-semibold text-slate-900 transition hover:bg-amber-400 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {checking ? 'Entrando…' : 'Entrar'}
        </button>
      </form>
    </div>
  );
}
