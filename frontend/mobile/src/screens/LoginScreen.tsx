/**
 * Acceso: pestañas PIN (suelo de venta) y usuario/contraseña. Ambas van
 * contra la misma API; el cliente no decide nada de permisos ni roles.
 */

import { useState } from 'react';
import { ApiError } from '../lib/api';
import { useSession } from '../state/session';
import Keypad from '../components/Keypad';

type Tab = 'pin' | 'password';

export default function LoginScreen() {
  const { loginWithPin, loginWithPassword } = useSession();
  const [tab, setTab] = useState<Tab>('pin');
  const [username, setUsername] = useState('');
  const [pin, setPin] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      if (tab === 'pin') {
        await loginWithPin(username.trim(), pin);
      } else {
        await loginWithPassword(username.trim(), password);
      }
    } catch (cause) {
      const message =
        cause instanceof ApiError && cause.status === 401
          ? 'Credenciales incorrectas.'
          : cause instanceof Error
            ? cause.message
            : 'No se pudo iniciar sesión.';
      setError(message);
      setPin('');
      setPassword('');
    } finally {
      setBusy(false);
    }
  }

  const canSubmit =
    !busy && username.trim().length > 0 && (tab === 'pin' ? pin.length >= 4 : password.length > 0);

  return (
    <div className="mx-auto flex h-dvh max-w-md flex-col justify-center gap-4 p-6">
      <div className="text-center">
        <div className="mx-auto mb-2 grid h-16 w-16 place-items-center rounded-2xl bg-teal-700 text-2xl text-white">
          🧾
        </div>
        <h1 className="text-xl font-semibold text-slate-800">TPV Móvil</h1>
        <p className="text-sm text-slate-500">Inicia sesión para continuar</p>
      </div>

      <div className="grid grid-cols-2 rounded-xl bg-slate-200 p-1 text-sm font-medium">
        {(
          [
            ['pin', 'PIN'],
            ['password', 'Contraseña'],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => {
              setTab(value);
              setError(null);
            }}
            className={`rounded-lg py-2 ${tab === value ? 'bg-white text-teal-700 shadow' : 'text-slate-500'}`}
          >
            {label}
          </button>
        ))}
      </div>

      <label className="block text-sm font-medium text-slate-600">
        Usuario
        <input
          className="input-base mt-1"
          value={username}
          autoCapitalize="none"
          autoCorrect="off"
          onChange={(event) => setUsername(event.target.value)}
        />
      </label>

      {tab === 'pin' ? (
        <div>
          <p className="mb-1 text-sm font-medium text-slate-600">PIN</p>
          <p className="input-base mb-2 tracking-[0.5em]" aria-live="polite">
            {'•'.repeat(pin.length) || <span className="tracking-normal text-slate-400">—</span>}
          </p>
          <Keypad
            onDigit={(digit) => setPin((current) => (current.length < 8 ? current + digit : current))}
            hideDot
            onBackspace={() => setPin((current) => current.slice(0, -1))}
          />
        </div>
      ) : (
        <label className="block text-sm font-medium text-slate-600">
          Contraseña
          <input
            className="input-base mt-1"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
      )}

      {error && (
        <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          {error}
        </div>
      )}

      <button type="button" className="btn-primary" disabled={!canSubmit} onClick={() => void submit()}>
        {busy ? 'Entrando…' : 'Entrar'}
      </button>
    </div>
  );
}
