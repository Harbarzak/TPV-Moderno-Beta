/** Mensajes de estado reutilizables: error visible (nunca silenciado) y carga. */

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
      <p>{message}</p>
      {onRetry && (
        <button type="button" className="btn-secondary mt-2 w-full" onClick={onRetry}>
          Reintentar
        </button>
      )}
    </div>
  );
}

export function Spinner({ label = 'Cargando…' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 p-6 text-sm text-slate-500">
      <span
        aria-hidden
        className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-teal-700"
      />
      {label}
    </div>
  );
}

export function EmptyNote({ children }: { children: React.ReactNode }) {
  return <p className="rounded-xl bg-slate-100 p-4 text-center text-sm text-slate-500">{children}</p>;
}
