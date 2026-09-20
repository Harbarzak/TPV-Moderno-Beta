/**
 * Menú de acciones de una mesa (§5.4): hoja inferior con objetivos táctiles
 * grandes — en tablet un popover flotante es un tiro al pie. Las acciones
 * disponibles dependen del estado (libre ⇒ abrir; abierta ⇒ traspasar,
 * juntar, dividir, cuenta, nota, cobrar).
 */

import * as Dialog from '@radix-ui/react-dialog';
import {
  ArrowLeftRight,
  Banknote,
  Merge,
  Plus,
  ReceiptText,
  Split,
  StickyNote,
  Undo2,
  type LucideIcon,
} from 'lucide-react';

export type FloorAction =
  | 'open'
  | 'enter'
  | 'transfer'
  | 'merge'
  | 'split'
  | 'bill-request'
  | 'bill-cancel'
  | 'session'
  | 'charge';

export interface FloorMenuAction {
  action: FloorAction;
  label: string;
  icon: LucideIcon;
}

export function actionsForTable(status: 'free' | 'open' | 'bill'): FloorMenuAction[] {
  if (status === 'free') {
    return [{ action: 'open', label: 'Abrir mesa', icon: Plus }];
  }
  const actions: FloorMenuAction[] = [
    { action: 'charge', label: 'Cobrar', icon: Banknote },
    { action: 'session', label: 'Nota y comensales', icon: StickyNote },
    { action: 'transfer', label: 'Traspasar a otra mesa', icon: ArrowLeftRight },
    { action: 'merge', label: 'Juntar con otra mesa', icon: Merge },
  ];
  if (status === 'bill') {
    actions.push({ action: 'bill-cancel', label: 'Quitar cuenta pedida', icon: Undo2 });
  } else {
    actions.push({ action: 'bill-request', label: 'Pedir la cuenta', icon: ReceiptText });
  }
  actions.push({ action: 'split', label: 'Dividir cuenta', icon: Split });
  return actions;
}

interface FloorTableMenuProps {
  tableName: string;
  actions: FloorMenuAction[];
  onAction: (action: FloorAction) => void;
  onClose: () => void;
}

export default function FloorTableMenu({ tableName, actions, onAction, onClose }: FloorTableMenuProps) {
  return (
    <Dialog.Root open onOpenChange={(next) => next || onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed bottom-0 left-1/2 w-[min(26rem,100vw)] -translate-x-1/2 rounded-t-2xl bg-surface-raised p-4 shadow-2xl">
          <Dialog.Title className="text-center text-lg font-bold text-ink">Mesa {tableName}</Dialog.Title>
          <Dialog.Description className="sr-only">Acciones de la mesa</Dialog.Description>
          <ul className="mt-3 flex flex-col gap-1.5">
            {actions.map(({ action, label, icon: Icon }) => (
              <li key={action}>
                <button
                  type="button"
                  onClick={() => {
                    onClose();
                    onAction(action);
                  }}
                  className="flex h-12 w-full items-center gap-3 rounded-xl bg-surface px-4 text-left font-semibold text-ink transition hover:bg-surface-hover active:scale-[0.98]"
                >
                  <Icon className="size-5 shrink-0 text-ink-muted" aria-hidden />
                  {label}
                </button>
              </li>
            ))}
          </ul>
          <Dialog.Close asChild>
            <button
              type="button"
              className="mt-3 h-12 w-full rounded-xl bg-surface-sunken font-medium text-ink-muted transition hover:bg-surface-hover"
            >
              Cerrar
            </button>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
