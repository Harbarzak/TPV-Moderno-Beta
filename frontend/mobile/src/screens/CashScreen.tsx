/**
 * Caja del terminal: apertura, informe X, movimientos de efectivo, arqueo y
 * cierre Z. Todo cálculo (esperado, diferencia, cambio) lo hace el backend;
 * el recuento por denominaciones se envía tal cual y la suma que se ve en
 * pantalla es SOLO un eco de lo tecleado.
 */

import { useCallback, useEffect, useState } from 'react';
import { z } from 'zod';
import { apiFetch, ApiError } from '../lib/api';
import { formatMoney, parseAmount } from '../lib/money';
import { can } from '../lib/permissions';
import { getTerminalId, isValidUuid } from '../lib/terminal';
import {
  cashReportSchema,
  cashSessionSchema,
  type CashReport,
  type CashSession,
} from '../lib/schemas';
import type { Me } from '../lib/schemas';
import Screen from '../components/Screen';
import { EmptyNote, ErrorBox, Spinner } from '../components/Feedback';
import Keypad from '../components/Keypad';

const DENOMINATIONS = [
  '0.01', '0.02', '0.05', '0.10', '0.20', '0.50',
  '1.00', '2.00', '5.00', '10.00', '20.00', '50.00',
  '100.00', '200.00', '500.00',
] as const;

type Panel = 'summary' | 'movement' | 'count';

export default function CashScreen({ me }: { me: Me }) {
  const canMove = can(me.permissions, 'cash.movements');
  const canClose = can(me.permissions, 'cash.close');

  const [session, setSession] = useState<CashSession | null>(null);
  const [report, setReport] = useState<CashReport | null>(null);
  const [checked, setChecked] = useState(false); // ¿ya sondeamos la sesión?
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>('summary');

  const terminalId = getTerminalId();
  const terminalOk = terminalId !== null && isValidUuid(terminalId);

  const loadSession = useCallback(async () => {
    if (!terminalOk) return;
    setError(null);
    try {
      const raw = await apiFetch<unknown>(`/api/v1/cash/sessions/current?terminal_id=${terminalId}`);
      const current = cashSessionSchema.parse(raw);
      setSession(current);
      setReport(
        cashReportSchema.parse(await apiFetch<unknown>(`/api/v1/cash/sessions/${current.id}/report`)),
      );
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) {
        setSession(null);
        setReport(null);
      } else {
        throw cause;
      }
    } finally {
      setChecked(true);
    }
  }, [terminalOk]);

  useEffect(() => {
    loadSession().catch((cause: unknown) =>
      setError(cause instanceof Error ? cause.message : 'No se pudo consultar la caja.'),
    );
  }, [loadSession]);

  if (!terminalOk) {
    return (
      <Screen title="Caja">
        <EmptyNote>Configura el terminal en Ajustes para operar la caja.</EmptyNote>
      </Screen>
    );
  }

  return (
    <Screen title="Caja">
      {error && <ErrorBox message={error} onRetry={() => void loadSession()} />}

      {!checked ? (
        <Spinner />
      ) : session === null ? (
        <OpenForm terminalId={terminalId} onOpened={() => void loadSession()} />
      ) : panel === 'summary' ? (
        <Summary
          session={session}
          report={report}
          canMove={canMove}
          canClose={canClose}
          onPanel={setPanel}
        />
      ) : panel === 'movement' ? (
        <MovementForm
          sessionId={session.id}
          onDone={() => {
            setPanel('summary');
            void loadSession();
          }}
          onCancel={() => setPanel('summary')}
        />
      ) : (
        <CountForm
          sessionId={session.id}
          canClose={canClose}
          onDone={() => {
            setPanel('summary');
            void loadSession();
          }}
          onCancel={() => setPanel('summary')}
        />
      )}
    </Screen>
  );
}

// -- apertura -------------------------------------------------------------------
function OpenForm({ terminalId, onOpened }: { terminalId: string; onOpened: () => void }) {
  const [amount, setAmount] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const parsed = parseAmount(amount);
  const valid = parsed !== null && parsed !== '0.00' ? parsed : amount === '' ? '0.00' : null;

  async function open() {
    if (valid === null || busy) return;
    setBusy(true);
    setError(null);
    try {
      await apiFetch('/api/v1/cash/sessions', {
        method: 'POST',
        body: JSON.stringify({ terminal_id: terminalId, opening_amount: valid }),
      });
      onOpened();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'No se pudo abrir la caja.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="card text-center">
        <p className="text-sm text-slate-500">Sin caja abierta en este terminal</p>
        <p className="mt-1 text-2xl font-semibold text-slate-800">
          {amount === '' ? '0,00 €' : `${amount} €`}
        </p>
        <p className="text-xs text-slate-500">Fondo inicial (puede ser 0)</p>
      </div>
      <Keypad
        onDigit={(digit) => setAmount((current) => current.length < 8 ? current + digit : current)}
        onDot={() => setAmount((current) => (current.includes(',') || current === '' ? current : `${current},`))}
        onBackspace={() => setAmount((current) => current.slice(0, -1))}
      />
      {error && <ErrorBox message={error} />}
      <button type="button" className="btn-primary w-full" disabled={busy || valid === null} onClick={() => void open()}>
        Abrir caja
      </button>
    </div>
  );
}

// -- resumen (X) ------------------------------------------------------------------
function Summary({
  session,
  report,
  canMove,
  canClose,
  onPanel,
}: {
  session: CashSession;
  report: CashReport | null;
  canMove: boolean;
  canClose: boolean;
  onPanel: (panel: Panel) => void;
}) {
  if (report === null) return <Spinner />;
  return (
    <div className="space-y-3">
      <div className="card space-y-1.5">
        <AmountRow label="Fondo inicial" value={report.opening_amount} />
        <AmountRow label="Ventas en efectivo" value={report.cash_sales} />
        <AmountRow label="Entradas" value={report.cash_in} />
        <AmountRow label="Salidas" value={report.cash_out} />
        <div className="border-t border-slate-100 pt-1.5">
          <AmountRow label="Efectivo esperado" value={report.expected_cash} strong />
        </div>
        {report.method_totals.map((total) => (
          <AmountRow key={total.code} label={`${total.code} (${total.sales_count})`} value={total.sales_total} />
        ))}
      </div>

      <div className="grid grid-cols-2 gap-2">
        {canMove && (
          <button type="button" className="btn-secondary" onClick={() => onPanel('movement')}>
            Entrada / salida
          </button>
        )}
        {canClose && (
          <button type="button" className="btn-secondary" onClick={() => onPanel('count')}>
            Arqueo {canClose ? '/ Cierre Z' : ''}
          </button>
        )}
      </div>

      {report.movements.length > 0 && (
        <div>
          <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Movimientos
          </h2>
          <ul className="divide-y divide-slate-100 rounded-2xl border border-slate-200 bg-white">
            {report.movements.slice().reverse().slice(0, 10).map((movement) => (
              <li key={movement.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                <div className="min-w-0">
                  <p className="truncate text-sm text-slate-700">{movement.reason}</p>
                  <p className="text-xs text-slate-400">{movement.kind === 'in' ? 'Entrada' : 'Salida'}</p>
                </div>
                <p className={`shrink-0 text-sm font-semibold ${movement.kind === 'in' ? 'text-teal-700' : 'text-rose-600'}`}>
                  {movement.kind === 'in' ? '+' : '−'} {formatMoney(movement.amount)}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-center text-xs text-slate-400">
        Sesión abierta {new Date(session.opened_at).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })}
      </p>
    </div>
  );
}

// -- movimiento -------------------------------------------------------------------
function MovementForm({
  sessionId,
  onDone,
  onCancel,
}: {
  sessionId: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState<'in' | 'out'>('in');
  const [amount, setAmount] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const parsed = parseAmount(amount);

  async function submit() {
    if (parsed === null || reason.trim() === '' || busy) return;
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/v1/cash/sessions/${sessionId}/movements`, {
        method: 'POST',
        body: JSON.stringify({ kind, amount: parsed, reason: reason.trim() }),
      });
      onDone();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'No se pudo registrar el movimiento.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 rounded-xl bg-slate-200 p-1 text-sm font-medium">
        {(['in', 'out'] as const).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => setKind(value)}
            className={`rounded-lg py-2 ${kind === value ? 'bg-white text-teal-700 shadow' : 'text-slate-500'}`}
          >
            {value === 'in' ? 'Entrada' : 'Salida'}
          </button>
        ))}
      </div>

      <p className="text-center text-2xl font-semibold text-slate-800" aria-live="polite">
        {amount === '' ? '0,00 €' : `${amount} €`}
      </p>
      <Keypad
        onDigit={(digit) => setAmount((current) => (current.length < 8 ? current + digit : current))}
        onDot={() => setAmount((current) => (current.includes(',') || current === '' ? current : `${current},`))}
        onBackspace={() => setAmount((current) => current.slice(0, -1))}
      />

      <input
        className="input-base"
        placeholder="Motivo"
        value={reason}
        onChange={(event) => setReason(event.target.value)}
      />
      {error && <ErrorBox message={error} />}

      <div className="grid grid-cols-2 gap-2">
        <button type="button" className="btn-secondary" onClick={onCancel}>Volver</button>
        <button
          type="button"
          className="btn-primary"
          disabled={busy || parsed === null || reason.trim() === ''}
          onClick={() => void submit()}
        >
          Registrar
        </button>
      </div>
    </div>
  );
}

// -- arqueo / cierre Z --------------------------------------------------------------
function CountForm({
  sessionId,
  canClose,
  onDone,
  onCancel,
}: {
  sessionId: string;
  canClose: boolean;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [quantities, setQuantities] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ counted: string; expected: string | null; difference: string | null } | null>(null);

  const lines = DENOMINATIONS.map((denomination) => ({
    denomination,
    quantity: quantities[denomination] ?? 0,
  })).filter((line) => line.quantity > 0);

  // Eco de lo tecleado; el importe oficial lo recalcula el servidor.
  const preview = lines.reduce(
    (sum, line) => sum + Number(line.denomination) * line.quantity,
    0,
  );

  async function register(close: boolean) {
    if (lines.length === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      const body = JSON.stringify({ lines: lines.map((line) => ({ denomination: line.denomination, quantity: line.quantity })) });
      const raw = await apiFetch<unknown>(
        close ? `/api/v1/cash/sessions/${sessionId}/close` : `/api/v1/cash/sessions/${sessionId}/counts`,
        { method: 'POST', body },
      );
      const parsed = countResultSchema.parse(raw);
      setResult({
        counted: parsed.counted_amount,
        expected: parsed.expected_amount ?? null,
        difference: parsed.difference ?? null,
      });
      if (close) {
        setTimeout(onDone, 1600);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'No se pudo registrar el arqueo.');
    } finally {
      setBusy(false);
    }
  }

  if (result !== null) {
    return (
      <div className="card text-center">
        <p className="text-lg font-semibold text-slate-800">Arqueo registrado</p>
        <div className="mt-2 space-y-1 text-sm">
          <AmountRow label="Contado" value={result.counted} strong />
          {result.expected !== null && <AmountRow label="Esperado" value={result.expected} />}
          {result.difference !== null && <AmountRow label="Diferencia" value={result.difference} />}
        </div>
        <button type="button" className="btn-secondary mt-4 w-full" onClick={onDone}>
          Volver al resumen
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-center text-2xl font-semibold text-slate-800" aria-live="polite">
        {preview.toLocaleString('es-ES', { minimumFractionDigits: 2 })} €
      </p>
      <ul className="divide-y divide-slate-100 rounded-2xl border border-slate-200 bg-white">
        {DENOMINATIONS.map((denomination) => (
          <li key={denomination} className="flex items-center justify-between px-4 py-2">
            <span className="text-sm text-slate-700">{formatMoney(denomination)}</span>
            <div className="flex items-center gap-3">
              <button
                type="button"
                className="btn-secondary h-9 w-9 !min-h-0 p-0 text-lg"
                onClick={() =>
                  setQuantities((current) => ({
                    ...current,
                    [denomination]: Math.max(0, (current[denomination] ?? 0) - 1),
                  }))
                }
              >
                −
              </button>
              <span className="w-8 text-center text-sm font-semibold" aria-live="polite">
                {quantities[denomination] ?? 0}
              </span>
              <button
                type="button"
                className="btn-secondary h-9 w-9 !min-h-0 p-0 text-lg"
                onClick={() =>
                  setQuantities((current) => ({
                    ...current,
                    [denomination]: (current[denomination] ?? 0) + 1,
                  }))
                }
              >
                +
              </button>
            </div>
          </li>
        ))}
      </ul>

      {error && <ErrorBox message={error} />}

      <div className="grid grid-cols-2 gap-2">
        <button type="button" className="btn-secondary" onClick={onCancel}>Volver</button>
        <button
          type="button"
          className="btn-primary"
          disabled={busy || lines.length === 0}
          onClick={() => void register(false)}
        >
          Guardar arqueo
        </button>
      </div>
      {canClose && (
        <button
          type="button"
          className="btn-danger w-full"
          disabled={busy || lines.length === 0}
          onClick={() => void register(true)}
        >
          Cerrar caja (Z)
        </button>
      )}
      {!canClose && (
        <p className="text-center text-xs text-slate-400">
          Sin permiso `cash.close` puedes arquear, pero el cierre lo hace quien lo tenga.
        </p>
      )}
    </div>
  );
}

/** Respuesta de arqueo/cierre: el servidor recalcula contado, esperado y cuadre. */
const countResultSchema = z.object({
  counted_amount: z.string().regex(/^\d{1,10}\.\d{2}$/),
  expected_amount: z.string().regex(/^\d{1,10}\.\d{2}$/).nullable().optional(),
  difference: z.string().regex(/^\d{1,10}\.\d{2}$/).nullable().optional(),
});

function AmountRow({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className={strong ? 'font-semibold text-slate-800' : 'text-slate-600'}>{label}</span>
      <span className={strong ? 'font-semibold text-teal-700' : 'text-slate-700'}>
        {formatMoney(value)}
      </span>
    </div>
  );
}
