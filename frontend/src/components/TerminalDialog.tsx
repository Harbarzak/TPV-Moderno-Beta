/**
 * Terminal de este puesto: etiqueta visible + UUID que exige /sales/orders.
 * «Cargar terminales» usa GET /admin/terminals si el usuario tiene el permiso
 * (admin); si responde 403, se introduce el UUID a mano (patrón del móvil,
 * fase 16). Es configuración local del puesto, no lógica de negocio.
 */

import { useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Monitor } from 'lucide-react';
import { ApiError, apiFetch } from '../lib/api';
import { isValidUuid, useTerminal } from '../state/terminal';
import { toast } from '../state/toasts';

interface TerminalDialogProps {
  onClose: () => void;
}

interface TerminalListItem {
  id: string;
  code: string;
  name: string;
  active: boolean;
}

export default function TerminalDialog({ onClose }: TerminalDialogProps) {
  const terminal = useTerminal((state) => state.terminal);
  const setTerminal = useTerminal((state) => state.set);
  const clearTerminal = useTerminal((state) => state.clear);

  const [label, setLabel] = useState(terminal?.label ?? '');
  const [id, setId] = useState(terminal?.id ?? '');
  const [list, setList] = useState<TerminalListItem[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const idOk = isValidUuid(id.trim());

  const save = (nextLabel: string, nextId: string) => {
    setTerminal({ id: nextId, label: nextLabel.trim() || 'Terminal' });
    toast.success(`Terminal configurado: ${nextLabel.trim() || 'Terminal'}`, { key: 'terminal' });
    onClose();
  };

  const loadList = async () => {
    setListError(null);
    try {
      const response = await apiFetch<{ items: TerminalListItem[] }>('/api/v1/admin/terminals');
      const active = response.items.filter((item) => item.active);
      setList(active);
      if (active.length === 0) setListError('No hay terminales activas dadas de alta.');
    } catch (err) {
      setListError(
        err instanceof ApiError && err.status === 403
          ? 'Sin permiso para listar terminales: introduce el UUID a mano.'
          : `No se pudo cargar el listado: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  };

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[min(26rem,94vw)] -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-surface-raised p-5 shadow-2xl">
          <Dialog.Title className="flex items-center gap-2 text-lg font-bold text-ink">
            <Monitor className="size-5 text-accent" aria-hidden />
            Terminal de este puesto
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-ink-muted">
            Toda venta se asocia a un terminal del servidor. Se guarda en este navegador.
          </Dialog.Description>

          <label className="mb-1 mt-4 block text-sm font-medium text-ink" htmlFor="terminal-label">
            Nombre visible
          </label>
          <input
            id="terminal-label"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="Barra 1"
            className="mb-3 w-full rounded-lg border border-surface-sunken bg-surface px-4 py-2.5 text-base text-ink outline-none focus:border-accent-hover"
          />

          <label className="mb-1 block text-sm font-medium text-ink" htmlFor="terminal-id">
            UUID del terminal
          </label>
          <input
            id="terminal-id"
            value={id}
            onChange={(event) => setId(event.target.value)}
            placeholder="00000000-0000-0000-0000-000000000000"
            spellCheck={false}
            className="w-full rounded-lg border border-surface-sunken bg-surface px-4 py-2.5 font-mono text-sm text-ink outline-none focus:border-accent-hover"
          />
          {id.trim() !== '' && !idOk && (
            <p role="alert" className="mt-1 text-sm text-danger-text">
              El UUID no tiene el formato correcto.
            </p>
          )}

          <button
            type="button"
            onClick={() => void loadList()}
            className="mt-3 w-full rounded-lg bg-surface-sunken px-4 py-2.5 text-sm font-medium text-ink transition hover:bg-surface-hover"
          >
            Cargar terminales del servidor
          </button>
          {listError && <p className="mt-2 text-xs text-ink-muted">{listError}</p>}

          {list !== null && list.length > 0 && (
            <ul className="mt-2 max-h-40 overflow-y-auto rounded-lg border border-surface-sunken">
              {list.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => save(item.name, item.id)}
                    className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left text-sm text-ink transition hover:bg-surface-sunken"
                  >
                    <span className="truncate font-medium">{item.name}</span>
                    <span className="truncate font-mono text-xs text-ink-muted">{item.code}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}

          <div className="mt-5 flex items-center justify-between gap-3">
            {terminal ? (
              <button
                type="button"
                onClick={() => {
                  clearTerminal();
                  toast.info('Terminal desconfigurado', { key: 'terminal' });
                  onClose();
                }}
                className="rounded-lg px-3 py-2 text-sm text-danger-text transition hover:bg-red-950/40"
              >
                Quitar configuración
              </button>
            ) : (
              <span />
            )}
            <div className="flex gap-3">
              <Dialog.Close asChild>
                <button
                  type="button"
                  className="rounded-lg bg-surface-sunken px-5 py-2.5 font-medium text-ink transition hover:bg-surface-hover"
                >
                  Cancelar
                </button>
              </Dialog.Close>
              <button
                type="button"
                disabled={!idOk}
                onClick={() => save(label, id.trim())}
                className="rounded-lg bg-accent px-6 py-2.5 font-bold text-accent-ink transition hover:bg-accent-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
              >
                Guardar
              </button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
