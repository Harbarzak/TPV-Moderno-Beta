/**
 * Nota y comensales de la sesión de mesa (PATCH /restaurant/orders/{id}):
 * los dos datos «de sala» que el camarero corrige a mitad de servicio. Solo
 * se envían los campos que el usuario tocó (el PATCH es por campos presentes).
 */

import { useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Loader2 } from 'lucide-react';
import { ApiError, apiFetch } from '../../lib/api';
import type { FloorTable } from '../../lib/floor';
import { useFloor } from '../../state/floor';
import { toast } from '../../state/toasts';

interface FloorSessionDialogProps {
  table: FloorTable;
  onSaved: () => void;
  onClose: () => void;
}

export default function FloorSessionDialog({ table, onSaved, onClose }: FloorSessionDialogProps) {
  const reloadAll = useFloor((state) => state.reloadAll);
  const refreshActiveOrder = useFloor((state) => state.refreshActiveOrder);

  const [guests, setGuests] = useState(table.guest_count === null ? '' : String(table.guest_count));
  const [note, setNote] = useState(table.note ?? '');
  const [submitting, setSubmitting] = useState(false);

  const save = async () => {
    if (!table.order_id) return;
    const parsedGuests = Number.parseInt(guests, 10);
    const body: Record<string, unknown> = {
      note: note.trim(), // cadena vacía = borrar la nota (campo siempre enviado)
    };
    if (Number.isInteger(parsedGuests) && parsedGuests > 0) body.guest_count = parsedGuests;
    setSubmitting(true);
    try {
      await apiFetch(`/api/v1/restaurant/orders/${table.order_id}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
      toast.success('Sesión actualizada', { key: 'floor-session' });
      await reloadAll();
      await refreshActiveOrder();
      onSaved();
      onClose();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      toast.error('No se pudo actualizar la sesión', { key: 'floor-session', detail });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog.Root open onOpenChange={(next) => next || onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[min(24rem,94vw)] -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-surface-raised p-5 shadow-2xl">
          <Dialog.Title className="text-lg font-bold text-ink">Mesa {table.name}: nota y comensales</Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-ink-muted">
            Cambios visibles para toda la sala al refrescar el plano.
          </Dialog.Description>

          <label className="mt-4 block text-sm font-medium text-ink-muted" htmlFor="session-guests">
            Comensales
          </label>
          <input
            id="session-guests"
            type="number"
            inputMode="numeric"
            min={1}
            max={999}
            value={guests}
            onChange={(event) => setGuests(event.target.value)}
            className="mt-1 h-12 w-full rounded-xl bg-surface px-4 text-lg font-bold tabular-nums text-ink outline-none ring-accent-hover focus:ring-2"
          />

          <label className="mt-3 block text-sm font-medium text-ink-muted" htmlFor="session-note">
            Nota
          </label>
          <input
            id="session-note"
            type="text"
            maxLength={500}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            className="mt-1 h-12 w-full rounded-xl bg-surface px-4 text-ink outline-none ring-accent-hover focus:ring-2"
          />

          <div className="mt-5 flex items-center justify-between gap-3">
            <Dialog.Close asChild>
              <button
                type="button"
                disabled={submitting}
                className="rounded-lg bg-surface-sunken px-5 py-3 font-medium text-ink transition hover:bg-surface-hover disabled:opacity-40"
              >
                Cancelar
              </button>
            </Dialog.Close>
            <button
              type="button"
              onClick={() => void save()}
              disabled={submitting}
              className="flex h-12 items-center gap-2 rounded-xl bg-accent px-6 font-bold text-accent-ink transition hover:bg-accent-hover active:scale-[0.98] disabled:opacity-40"
            >
              {submitting && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Guardar
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
