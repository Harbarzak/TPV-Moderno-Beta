/**
 * Caja del terminal (design-system.md §5.3): si está cerrada, apertura con
 * fondo inicial contado (QA E01); si está abierta, su estado. El ARQUEO/cierre
 * se sigue haciendo desde el móvil o informes — aquí solo lo que el cobro
 * necesita: saber si hay caja abierta y abrirla.
 */

import { useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Lock, LockOpen } from 'lucide-react';
import { amountToCents, pressAmount, type KeypadKey } from '../lib/keypad';
import { formatMoney } from '../lib/money';
import { useCash } from '../state/cash';
import { useTerminal } from '../state/terminal';
import { toast } from '../state/toasts';
import Keypad from './Keypad';

interface CashDialogProps {
  onClose: () => void;
}

export default function CashDialog({ onClose }: CashDialogProps) {
  const session = useCash((state) => state.session);
  const openSession = useCash((state) => state.open);
  const terminal = useTerminal((state) => state.terminal);
  const [buffer, setBuffer] = useState('');

  const cents = amountToCents(buffer);
  const invalid = buffer !== '' && cents === null;

  const onKey = (key: KeypadKey) => setBuffer((current) => pressAmount(current, key));

  const open = async () => {
    if (!terminal || cents === null) return;
    try {
      await openSession(terminal.id, formatMoney(cents));
      toast.success(`Caja abierta con ${formatMoney(cents)} €`, { key: 'cash' });
      onClose();
    } catch (err) {
      toast.error('No se pudo abrir la caja', {
        key: 'cash',
        detail: err instanceof Error ? err.message : String(err),
      });
    }
  };

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[min(24rem,94vw)] -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-surface-raised p-5 shadow-2xl">
          {session?.status === 'open' ? (
            <>
              <Dialog.Title className="flex items-center gap-2 text-lg font-bold text-ink">
                <Lock className="size-5 text-success" aria-hidden />
                Caja abierta
              </Dialog.Title>
              <Dialog.Description className="mt-3 text-sm text-ink-muted">
                Fondo inicial: <span className="font-bold text-ink tabular-nums">{session.opening_amount} €</span>
                <br />
                Abierta: {new Date(session.opened_at).toLocaleTimeString('es-ES')}
              </Dialog.Description>
              <p className="mt-2 text-xs text-ink-muted">
                El arqueo y cierre de caja se hacen desde la app móvil o desde informes.
              </p>
              <Dialog.Close asChild>
                <button
                  type="button"
                  autoFocus
                  className="mt-5 w-full rounded-lg bg-surface-sunken px-5 py-3 font-medium text-ink transition hover:bg-surface-hover"
                >
                  Cerrar
                </button>
              </Dialog.Close>
            </>
          ) : (
            <>
              <Dialog.Title className="flex items-center gap-2 text-lg font-bold text-ink">
                <LockOpen className="size-5 text-accent" aria-hidden />
                Abrir caja
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-ink-muted">
                Fondo inicial contado en efectivo. El cobro lo exige (QA E01: una caja
                abierta por terminal).
              </Dialog.Description>

              <p className="mt-4 rounded-xl bg-surface px-4 py-3 text-right text-3xl font-bold text-ink tabular-nums">
                {buffer === '' ? '—' : `${buffer} €`}
              </p>
              {invalid && (
                <p role="alert" className="mt-2 text-sm text-danger-text">
                  Importe no válido.
                </p>
              )}

              <Keypad onKey={onKey} className="mt-3" />

              <div className="mt-3 flex justify-between gap-3">
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
                  onClick={() => void open()}
                  disabled={!terminal || cents === null}
                  title={terminal ? undefined : 'Configura primero el terminal de este puesto'}
                  className="rounded-lg bg-accent px-8 py-2.5 font-bold text-accent-ink transition hover:bg-accent-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Abrir caja
                </button>
              </div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
