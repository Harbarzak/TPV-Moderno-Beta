/**
 * Estadísticas SEGÚN PERMISOS (lo que pide la fase): con `cash.open`, el
 * informe X de la sesión abierta del terminal; con `reports.view`, el
 * histórico de cuadres Z. Todos los importes vienen calculados del servidor.
 * Sin permisos → mensaje; sin terminal configurado → aviso.
 */

import { useCallback, useEffect, useState } from 'react';
import { z } from 'zod';
import { apiFetch, ApiError } from '../lib/api';
import { formatMoney } from '../lib/money';
import { can } from '../lib/permissions';
import { getTerminalId, isValidUuid } from '../lib/terminal';
import {
  cashReportSchema,
  sessionListSchema,
  type CashReport,
  type CashSession,
} from '../lib/schemas';
import type { Me } from '../lib/schemas';
import Screen from '../components/Screen';
import { EmptyNote, ErrorBox, Spinner } from '../components/Feedback';

export default function StatsScreen({ me }: { me: Me }) {
  const shiftAllowed = can(me.permissions, 'cash.open');
  const historyAllowed = can(me.permissions, 'reports.view');

  const [report, setReport] = useState<CashReport | null>(null);
  const [noSession, setNoSession] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const terminalId = getTerminalId();
  const terminalOk = terminalId !== null && isValidUuid(terminalId);

  const loadShift = useCallback(async () => {
    if (!shiftAllowed || !terminalOk) return;
    setNoSession(false);
    setError(null);
    try {
      const raw = await apiFetch<unknown>(`/api/v1/cash/sessions/current?terminal_id=${terminalId}`);
      const session = cashSessionId.parse(raw);
      setReport(
        cashReportSchema.parse(await apiFetch<unknown>(`/api/v1/cash/sessions/${session.id}/report`)),
      );
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 404) {
        setReport(null);
        setNoSession(true);
        return;
      }
      throw cause;
    }
  }, [shiftAllowed, terminalOk]);

  useEffect(() => {
    loadShift().catch((cause: unknown) =>
      setError(cause instanceof Error ? cause.message : 'No se pudieron cargar las estadísticas.'),
    );
  }, [loadShift]);

  if (!shiftAllowed && !historyAllowed) {
    return (
      <Screen title="Estadísticas">
        <EmptyNote>Tu usuario no tiene permisos para consultar estadísticas.</EmptyNote>
      </Screen>
    );
  }

  return (
    <Screen title="Estadísticas">
      {error && <ErrorBox message={error} onRetry={() => void loadShift()} />}

      {shiftAllowed && (
        <section className="mb-4">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
            Turno actual (X)
          </h2>
          {!terminalOk ? (
            <EmptyNote>Configura el terminal en Ajustes para ver tu turno.</EmptyNote>
          ) : report === null ? (
            noSession ? (
              <EmptyNote>Sin caja abierta en este terminal.</EmptyNote>
            ) : (
              <Spinner />
            )
          ) : (
            <div className="card space-y-1.5">
              <AmountRow label="Fondo inicial" value={report.opening_amount} />
              <AmountRow label="Ventas en efectivo" value={report.cash_sales} />
              <AmountRow label="Entradas" value={report.cash_in} />
              <AmountRow label="Salidas" value={report.cash_out} />
              <div className="border-t border-slate-100 pt-1.5">
                <AmountRow label="Efectivo esperado" value={report.expected_cash} strong />
              </div>
              {report.method_totals.map((total) => (
                <AmountRow
                  key={total.code}
                  label={`${total.code} (${total.sales_count})`}
                  value={total.sales_total}
                />
              ))}
            </div>
          )}
        </section>
      )}

      {historyAllowed && <ZHistory />}
    </Screen>
  );
}

/** Zod mínimo para leer solo el ID de la sesión actual. */
const cashSessionId = z.object({ id: z.string().uuid() });

/** Histórico de cuadres Z: sesiones cerradas del servidor (reports.view). */
function ZHistory() {
  const [sessions, setSessions] = useState<CashSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    const data = sessionListSchema.parse(
      await apiFetch<unknown>('/api/v1/cash/sessions?status=closed&limit=20'),
    );
    setSessions(data.items);
  }, []);

  useEffect(() => {
    load().catch((cause: unknown) =>
      setError(cause instanceof Error ? cause.message : 'No se pudo cargar el histórico.'),
    );
  }, [load]);

  return (
    <section>
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
        Histórico (Z)
      </h2>
      {error && <ErrorBox message={error} onRetry={() => void load()} />}
      {sessions === null ? (
        <Spinner />
      ) : sessions.length === 0 ? (
        <EmptyNote>Aún no hay cierres.</EmptyNote>
      ) : (
        <ul className="divide-y divide-slate-100 rounded-2xl border border-slate-200 bg-white">
          {sessions.map((session) => (
            <li key={session.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div>
                <p className="text-sm font-medium text-slate-800">
                  {new Date(session.closed_at ?? session.opened_at).toLocaleString('es-ES', {
                    day: '2-digit',
                    month: '2-digit',
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </p>
                <p className="text-xs text-slate-500">
                  Contado {formatMoney(session.counted_amount ?? '0.00')}
                </p>
              </div>
              <p
                className={`text-sm font-semibold ${
                  session.difference !== null && session.difference !== '0.00'
                    ? 'text-rose-600'
                    : 'text-teal-700'
                }`}
              >
                {session.difference === null ? '—' : formatMoney(session.difference)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

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
