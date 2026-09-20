/**
 * Traspaso y junta de mesas (§5.4). Traspasar lleva la comanda intacta a una
 * mesa LIBRE; juntar vuelca sus líneas en la comanda de otra mesa ABIERTA
 * (el servidor anula el borrador origen y conserva el rastro de auditoría).
 */

import { useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Loader2 } from 'lucide-react';
import { ApiError, apiFetch } from '../../lib/api';
import type { FloorTable } from '../../lib/floor';
import { useFloor } from '../../state/floor';
import { toast } from '../../state/toasts';

interface FloorMoveDialogProps {
  table: FloorTable;
  mode: 'transfer' | 'merge';
  onDone: () => void;
  onClose: () => void;
}

export default function FloorMoveDialog({ table, mode, onDone, onClose }: FloorMoveDialogProps) {
  const tables = useFloor((state) => state.tables);
  const reloadAll = useFloor((state) => state.reloadAll);
  const setActiveTable = useFloor((state) => state.setActiveTable);
  const [submittingId, setSubmittingId] = useState<string | null>(null);

  const candidates = tables.filter(
    (candidate) =>
      candidate.active &&
      candidate.id !== table.id &&
      (mode === 'transfer' ? candidate.status === 'free' : candidate.status !== 'free'),
  );

  const move = async (target: FloorTable) => {
    setSubmittingId(target.id);
    try {
      await apiFetch(`/api/v1/restaurant/tables/${table.id}/${mode}`, {
        method: 'POST',
        body: JSON.stringify({ target_table_id: target.id }),
      });
      toast.success(
        mode === 'transfer'
          ? `Comanda traspasada a ${target.name}`
          : `${table.name} juntada con ${target.name}`,
        { key: 'floor-move' },
      );
      await reloadAll();
      // La mesa origen queda libre/anulada: de vuelta al mapa.
      await setActiveTable(null);
      onDone();
      onClose();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      toast.error('No se pudo mover la comanda', { key: 'floor-move', detail });
    } finally {
      setSubmittingId(null);
    }
  };

  return (
    <Dialog.Root open onOpenChange={(next) => next || onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 max-h-[80vh] w-[min(26rem,94vw)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl bg-surface-raised p-5 shadow-2xl">
          <Dialog.Title className="text-lg font-bold text-ink">
            {mode === 'transfer' ? `Traspasar ${table.name}` : `Juntar ${table.name} con…`}
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-ink-muted">
            {mode === 'transfer'
              ? 'Elige la mesa libre de destino: la comanda viaja entera.'
              : 'Elige la mesa abierta de destino: sus líneas se suman a la comanda destino.'}
          </Dialog.Description>

          {candidates.length === 0 && (
            <p className="py-6 text-sm text-ink-muted">
              {mode === 'transfer'
                ? 'No hay mesas libres ahora mismo.'
                : 'No hay otras mesas abiertas ahora mismo.'}
            </p>
          )}

          <ul className="mt-3 flex flex-col gap-2">
            {candidates.map((candidate) => (
              <li key={candidate.id}>
                <button
                  type="button"
                  onClick={() => void move(candidate)}
                  disabled={submittingId !== null}
                  className="flex h-12 w-full items-center justify-between rounded-xl bg-surface px-4 font-semibold text-ink transition hover:bg-surface-hover active:scale-[0.98] disabled:opacity-40"
                >
                  <span className="flex items-center gap-2">
                    {submittingId === candidate.id && (
                      <Loader2 className="size-4 animate-spin" aria-hidden />
                    )}
                    {candidate.name}
                    <span className="text-xs font-normal text-ink-muted">{candidate.zone_name}</span>
                  </span>
                  <span className="text-sm font-bold tabular-nums text-ink-muted">
                    {candidate.seats} plazas
                  </span>
                </button>
              </li>
            ))}
          </ul>

          <div className="mt-5 flex justify-end">
            <Dialog.Close asChild>
              <button
                type="button"
                disabled={submittingId !== null}
                className="rounded-lg bg-surface-sunken px-5 py-3 font-medium text-ink transition hover:bg-surface-hover disabled:opacity-40"
              >
                Cancelar
              </button>
            </Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
