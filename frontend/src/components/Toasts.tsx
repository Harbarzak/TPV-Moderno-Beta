/**
 * Contenedor de avisos (design-system.md §4): NO modal, arriba-centro, sobre
 * todo lo demás (z-70). Éxito/informativo caduca solo (4 s); el error es
 * persistente y se cierra a mano — nunca se esfuma un fallo de cobro.
 * El estado vive en state/toasts.ts; aquí solo el pintado.
 */

import { X } from 'lucide-react';
import { useToasts, type ToastItem } from '../state/toasts';

const STYLES: Record<ToastItem['kind'], string> = {
  success: 'bg-emerald-600 text-white',
  error: 'bg-red-600 text-white',
  info: 'bg-sky-600 text-white',
};

export default function Toasts() {
  const items = useToasts((state) => state.items);
  const dismiss = useToasts((state) => state.dismiss);
  if (items.length === 0) return null;

  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 top-2 z-toast flex flex-col items-center gap-2 px-3"
    >
      {items.map((toast) => (
        <div
          key={toast.id}
          role={toast.kind === 'error' ? 'alert' : 'status'}
          className={`pointer-events-auto flex w-full max-w-xl items-start gap-3 rounded-xl px-4 py-3 shadow-lg ${STYLES[toast.kind]}`}
        >
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold">{toast.message}</p>
            {toast.detail && <p className="mt-0.5 break-words text-xs opacity-90">{toast.detail}</p>}
          </div>
          <button
            type="button"
            onClick={() => dismiss(toast.id)}
            aria-label="Cerrar aviso"
            className="rounded p-1 opacity-80 transition hover:opacity-100"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>
      ))}
    </div>
  );
}
