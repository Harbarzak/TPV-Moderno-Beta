/**
 * Ayuda (F1): los atajos y gestos de la pantalla de venta, pensados para
 * usarse sin conocimientos técnicos (FASE_05).
 */

import * as Dialog from '@radix-ui/react-dialog';
import { CircleHelp, X } from 'lucide-react';

interface HelpOverlayProps {
  onClose: () => void;
}

const SHORTCUTS: Array<[string, string]> = [
  ['F1', 'Abrir esta ayuda'],
  ['F2', 'Buscar producto (nombre, alias o código de barras)'],
  ['F4', 'Cobrar el ticket (necesita terminal configurado y caja abierta)'],
  ['Tocar un botón', 'Añade 1 unidad al ticket'],
  ['Pasar un código', 'El lector añade el producto solo'],
  ['+ / −', 'Más o menos cantidad en el ticket (− hasta quitar la línea)'],
  ['% en la línea', 'Descuento de la línea (presets o importe a mano)'],
  ['Cliente', 'Pendiente de la API de clientes: hoy el ticket sale sin cliente'],
  ['Papelera', 'Quitar la línea del ticket'],
  ['Vaciar', 'Empezar un ticket nuevo (pide confirmación)'],
  ['Esc', 'Cerrar ventanas (el cobro en marcha no se cierra con Esc)'],
];

export default function HelpOverlay({ onClose }: HelpOverlayProps) {
  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[min(34rem,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-slate-800 p-6 shadow-2xl">
          <Dialog.Title className="mb-4 flex items-center gap-2 text-xl font-bold text-slate-100">
            <CircleHelp className="size-6 text-amber-400" aria-hidden />
            Cómo se usa el TPV
          </Dialog.Title>
          <Dialog.Description className="sr-only">Atajos y gestos disponibles.</Dialog.Description>

          <dl className="flex flex-col gap-2">
            {SHORTCUTS.map(([action, description]) => (
              <div key={action} className="flex gap-3 rounded-lg bg-slate-700/60 px-4 py-2.5">
                <dt className="w-40 shrink-0 font-semibold text-amber-300">{action}</dt>
                <dd className="text-slate-200">{description}</dd>
              </div>
            ))}
          </dl>

          <Dialog.Close
            className="absolute right-4 top-4 flex size-9 items-center justify-center rounded-lg bg-slate-700 text-slate-300 hover:bg-slate-600"
            aria-label="Cerrar ayuda"
          >
            <X className="size-5" aria-hidden />
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
