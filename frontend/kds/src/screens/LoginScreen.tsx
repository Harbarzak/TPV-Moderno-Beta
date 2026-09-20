/**
 * Acceso del KDS: usuario/contraseña contra la misma API. Sin PIN ni roles
 * en cliente: si el usuario no puede operar cocinas, el tablero le dirá 403.
 */

import { useState } from 'react';
import { ChefHat } from 'lucide-react';
import { ApiError } from '../lib/api';
import { useSession } from '../state/session';

export default function LoginScreen() {
  const { login } = useSession();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      await login(username.trim(), password);
    } catch (cause) {
      setError(
        cause instanceof ApiError && cause.status === 401
          ? 'Credenciales incorrectas.'
          : cause instanceof Error
            ? cause.message
            : 'No se pudo iniciar sesión.',
      );
      setPassword('');
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={(event) => void submit(event)}
      className="mx-auto flex h-dvh max-w-sm flex-col justify-center gap-4 p-6"
    >
      <div className="text-center">
        <div className="mx-auto mb-3 grid h-16 w-16 place-items-center rounded-2xl bg-orange-600 text-white">
          <ChefHat size={32} aria-hidden />
        </div>
        <h1 className="text-2xl font-bold">KDS · Cocina</h1>
        <p className="text-sm text-slate-400">Inicia sesión para ver las comandas</p>
      </div>

      <label className="block text-sm font-medium text-slate-300">
        Usuario
        <input
          className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2.5 text-base outline-none focus:border-orange-500 focus:ring-2 focus:ring-orange-500/20"
          value={username}
          autoCapitalize="none"
          autoCorrect="off"
          autoComplete="username"
          onChange={(event) => setUsername(event.target.value)}
        />
      </label>

      <label className="block text-sm font-medium text-slate-300">
        Contraseña
        <input
          className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2.5 text-base outline-none focus:border-orange-500 focus:ring-2 focus:ring-orange-500/20"
          type="password"
          value={password}
          autoComplete="current-password"
          onChange={(event) => setPassword(event.target.value)}
        />
      </label>

      {error && (
        <div role="alert" className="rounded-lg border border-rose-800 bg-rose-950 p-3 text-sm text-rose-200">
          {error}
        </div>
      )}

      <button
        type="submit"
        disabled={!busy && (username.trim().length === 0 || password.length === 0)}
        className="btn-touch min-h-[52px] bg-orange-600 text-lg text-white active:bg-orange-700 disabled:opacity-40"
      >
        {busy ? 'Entrando…' : 'Entrar'}
      </button>
    </form>
  );
}
