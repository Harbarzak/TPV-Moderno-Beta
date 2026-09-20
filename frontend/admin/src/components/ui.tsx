/**
 * Mini-librería de controles del panel (ratón/teclado, tema claro índigo).
 * Incluye los dos hooks que usan todas las páginas: useAsync (carga de
 * datos con recarga manual) y useAction (mutaciones con su error).
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react';

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** Carga declarativa: data | error | loading + reload() tras cada mutación. */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    load().then(
      (value) => {
        if (alive) {
          setData(value);
          setLoading(false);
        }
      },
      (e: unknown) => {
        if (alive) {
          setError(errorMessage(e));
          setLoading(false);
        }
      },
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

/** Mutación con estado de espera y error listo para el Banner. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async (fn: () => Promise<void>): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      return true;
    } catch (e) {
      setError(errorMessage(e));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  return { busy, error, run, clear: useCallback(() => setError(null), []) };
}

export function PageHeader({ title, hint, actions }: {
  title: string;
  hint?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-bold text-slate-800">{title}</h1>
        {hint && <p className="mt-0.5 text-sm text-slate-500">{hint}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}

export function Banner({ kind = 'error', onClose, children }: {
  kind?: 'error' | 'ok';
  onClose?: () => void;
  children: ReactNode;
}) {
  const tone =
    kind === 'error'
      ? 'border-rose-200 bg-rose-50 text-rose-800'
      : 'border-emerald-200 bg-emerald-50 text-emerald-800';
  return (
    <div role={kind === 'error' ? 'alert' : 'status'} className={`mb-4 flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-sm ${tone}`}>
      <span className="whitespace-pre-wrap">{children}</span>
      {onClose && (
        <button type="button" onClick={onClose} className="text-xs font-semibold opacity-60 hover:opacity-100" aria-label="Cerrar aviso">
          ✕
        </button>
      )}
    </div>
  );
}

export function Loading({ label = 'Cargando…' }: { label?: string }) {
  return <p className="py-8 text-center text-sm text-slate-400">{label}</p>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-8 text-center text-sm text-slate-400">{children}</p>;
}

export function Badge({ ok, on = 'Activo', off = 'Baja' }: { ok: boolean; on?: string; off?: string }) {
  return <span className={ok ? 'badge-ok' : 'badge-off'}>{ok ? on : off}</span>;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="label-base">{label}</span>
      {children}
    </label>
  );
}

export function Modal({ title, onClose, wide, children }: {
  title: string;
  onClose: () => void;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40 p-4" role="dialog" aria-modal="true" aria-label={title}>
      <div className={`max-h-[90dvh] w-full overflow-auto rounded-xl bg-white p-5 shadow-xl ${wide ? 'max-w-2xl' : 'max-w-md'}`}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-bold text-slate-800">{title}</h2>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-slate-600" aria-label="Cerrar">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** Fecha/hora local corta para tablas (created_at, occurred_at, …). */
export function fmtDate(value: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' });
}

/** Tamaño de backup legible (KB/MB). */
export function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
