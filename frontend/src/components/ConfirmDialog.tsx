/**
 * Confirmación destructiva (design-system.md §4): AlertDialog conceptual sobre
 * el Dialog ya instalado — la consecuencia va en el título y el botón de
 * riesgo en rojo. Cancelar es la acción por defecto (Esc incluido).
 */

import * as Dialog from '@radix-ui/react-dialog';
import { CircleAlert } from 'lucide-react';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onOpenChange: (open: boolean) => void;
}

export default function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  danger = true,
  onConfirm,
  onOpenChange,
}: ConfirmDialogProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[min(28rem,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-surface-raised p-6 shadow-2xl">
          <Dialog.Title className="flex items-center gap-2 text-lg font-bold text-ink">
            {danger && <CircleAlert className="size-5 shrink-0 text-danger-text" aria-hidden />}
            {title}
          </Dialog.Title>
          <Dialog.Description className="mt-2 text-sm text-ink-muted">{description}</Dialog.Description>

          <div className="mt-6 flex justify-end gap-3">
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
              autoFocus
              onClick={() => {
                onConfirm();
                onOpenChange(false);
              }}
              className={`rounded-lg px-5 py-2.5 font-semibold text-white transition active:scale-[0.98] ${
                danger ? 'bg-danger hover:bg-red-500' : 'bg-accent text-accent-ink hover:bg-accent-hover'
              }`}
            >
              {confirmLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
