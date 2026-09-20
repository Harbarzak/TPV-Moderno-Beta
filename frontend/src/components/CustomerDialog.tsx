/**
 * Cliente del ticket. HOY es un hueco honesto: la tabla y el permiso existen
 * (models/catalog.py: Customer; customers.view/edit) pero NO hay API de
 * clientes — es la P1 de la revisión de arquitectura (fase propia). El pedido
 * (OrderCreate.customer_id) la consumirá cuando exista; mientras tanto el
 * botón explica el estado en vez de fingir una búsqueda vacía.
 */

import * as Dialog from '@radix-ui/react-dialog';
import { UserRound } from 'lucide-react';

interface CustomerDialogProps {
  onClose: () => void;
}

export default function CustomerDialog({ onClose }: CustomerDialogProps) {
  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[min(24rem,94vw)] -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-surface-raised p-5 shadow-2xl">
          <Dialog.Title className="flex items-center gap-2 text-lg font-bold text-ink">
            <UserRound className="size-5 text-accent" aria-hidden />
            Cliente del ticket
          </Dialog.Title>
          <Dialog.Description className="mt-2 text-sm text-ink-muted">
            La asignación de cliente llega con la API de clientes (pendiente, fase
            propia de la revisión de arquitectura). Hoy el ticket sale sin cliente;
            recuerda que la factura exige uno asignado.
          </Dialog.Description>
          <Dialog.Close asChild>
            <button
              type="button"
              autoFocus
              className="mt-5 w-full rounded-lg bg-surface-sunken px-5 py-3 font-medium text-ink transition hover:bg-surface-hover"
            >
              Entendido
            </button>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
