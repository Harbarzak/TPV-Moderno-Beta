/**
 * Ajustes del dispositivo: terminal asociado (configuración local mientras no
 * exista el módulo de terminales, fase 21) y cierre de sesión. Nada de negocio.
 */

import { useState } from 'react';
import { getTerminalId, isValidUuid, setTerminalId } from '../lib/terminal';
import { useSession } from '../state/session';
import type { Me } from '../lib/schemas';
import Screen from '../components/Screen';

export default function SettingsScreen({ me }: { me: Me }) {
  const logout = useSession((state) => state.logout);
  const [terminal, setTerminal] = useState(getTerminalId() ?? '');
  const [saved, setSaved] = useState(false);
  const valid = isValidUuid(terminal.trim());

  return (
    <Screen title="Ajustes">
      <div className="space-y-4">
        <section className="card">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Sesión</h2>
          <dl className="mt-2 space-y-1 text-sm">
            <Row label="Usuario" value={me.username} />
            <Row label="Nombre" value={me.full_name} />
            <Row label="Rol" value={me.role} />
            <Row label="Ámbito" value={me.scope} />
          </dl>
        </section>

        <section className="card">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Terminal</h2>
          <p className="mt-1 text-xs text-slate-500">
            UUID del terminal de este dispositivo (lo valida el backend en cada operación).
          </p>
          <input
            className="input-base mt-2 font-mono text-sm"
            placeholder="00000000-0000-0000-0000-000000000000"
            value={terminal}
            autoCapitalize="none"
            spellCheck={false}
            onChange={(event) => {
              setTerminal(event.target.value);
              setSaved(false);
            }}
          />
          <button
            type="button"
            className="btn-primary mt-2 w-full"
            disabled={!valid}
            onClick={() => {
              setTerminalId(terminal.trim());
              setSaved(true);
            }}
          >
            {valid ? 'Guardar terminal' : 'UUID no válido'}
          </button>
          {saved && <p className="mt-2 text-center text-xs text-teal-700">Terminal guardado.</p>}
        </section>

        <button type="button" className="btn-danger w-full" onClick={() => void logout()}>
          Cerrar sesión
        </button>
      </div>
    </Screen>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-slate-500">{label}</dt>
      <dd className="truncate font-medium text-slate-800">{value}</dd>
    </div>
  );
}
