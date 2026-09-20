/**
 * Apertura de mesa (§5.4): comensales + nota y a la comanda. La sesión de
 * mesa es un borrador en el servidor (order_type ``restaurant``): abrir es
 * un POST idempotente — el reintento no duplica la comanda.
 */

import { useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Loader2 } from 'lucide-react';
import { ApiError, apiFetch } from '../../lib/api';
import type { FloorTable } from '../../lib/floor';
import { useTerminal } from '../../state/terminal';
import { toast } from '../../state/toasts';

interface FloorOpenDialogProps {
  table: FloorTable;
  onOpened: () => void;
  onClose: () => void;
}

export default function FloorOpenDialog({ table, onOpened, onClose }: FloorOpenDialogProps) {
  const terminal = useTerminal((state) => state.terminal);
  const [guests, setGuests] = useState(String(table.guest_count ?? table.seats));
  const [note, setNote] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const open = async () => {
    if (!terminal) {
      toast.info('Configura primero el terminal de este puesto', { key: 'floor-open' });
      return;
    }
    const parsedGuests = Number.parseInt(guests, 10);
    setSubmitting(true);
    try {
      await apiFetch(`/api/v1/restaurant/tables/${table.id}/open`, {
        method: 'POST',
        body: JSON.stringify({
          terminal_id: terminal.id,
          guest_count: Number.isInteger(parsedGuests) && parsedGuests > 0 ? parsedGuests : null,
          note: note.trim() === '' ? null : note.trim(),
        }),
      }, { idempotencyKey: crypto.randomUUID() });
      toast.success(`Mesa ${table.name} abierta`, { key: 'floor-open' });
      onOpened();
      onClose();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      toast.error(`No se pudo abrir la mesa ${table.name}`, { key: 'floor-open', detail });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog.Root open onOpenChange={(next) => next || onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[min(24rem,94vw)] -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-surface-raised p-5 shadow-2xl">
          <Dialog.Title className="text-lg font-bold text-ink">Abrir mesa {table.name}</Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-ink-muted">
            {table.seats} plazas · {table.zone_name}
          </Dialog.Description>

          <label className="mt-4 block text-sm font-medium text-ink-muted" htmlFor="floor-guests">
            Comensales
          </label>
          <input
            id="floor-guests"
            type="number"
            inputMode="numeric"
            min={1}
            max={999}
            value={guests}
            onChange={(event) => setGuests(event.target.value)}
            className="mt-1 h-12 w-full rounded-xl bg-surface px-4 text-lg font-bold tabular-nums text-ink outline-none ring-accent-hover focus:ring-2"
          />

          <label className="mt-3 block text-sm font-medium text-ink-muted" htmlFor="floor-note">
            Nota (opcional)
          </label>
          <input
            id="floor-note"
            type="text"
            maxLength={500}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Alergias, celebración…"
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
              onClick={() => void open()}
              disabled={submitting}
              className="flex h-12 items-center gap-2 rounded-xl bg-accent px-6 font-bold text-accent-ink transition hover:bg-accent-hover active:scale-[0.98] disabled:opacity-40"
            >
              {submitting && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Abrir mesa
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
