/**
 * Descuento de línea (design-system.md §4/§5.2): presets rápidos + importe a
 * mano con el keypad. El permiso «orders.discount» lo juzga el backend al
 * cobrar — aquí no hay forma honesta de saberlo de antemano.
 */

import { useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Percent } from 'lucide-react';
import { amountToCents, centsToBuffer, pressAmount, type KeypadKey } from '../lib/keypad';
import { rateToContract } from '../lib/checkout';
import { formatMoney, lineTotal, lineTotalDiscounted, parseMoney, pctToBp } from '../lib/money';
import { useCart, type CartLine } from '../state/cart';
import { toast } from '../state/toasts';
import Keypad from './Keypad';

interface DiscountDialogProps {
  line: CartLine;
  onClose: () => void;
}

const PRESETS = ['5', '10', '15', '20'];

export default function DiscountDialog({ line, onClose }: DiscountDialogProps) {
  const setDiscount = useCart((state) => state.setDiscount);
  const [buffer, setBuffer] = useState(() =>
    line.discountPct !== '0.00' ? centsToBuffer(BigInt(line.discountPct.replace('.', ''))) : '',
  );

  const priceCents = parseMoney(line.price);
  const base = lineTotal(priceCents, line.qtyMilli);
  const error = validate(buffer);

  const onKey = (key: KeypadKey) => setBuffer((current) => pressAmount(current, key));

  const apply = () => {
    if (error || buffer === '') return;
    const pct = rateToContract(buffer);
    setDiscount(line.lineId, pct);
    toast.success(pct === '0.00' ? 'Descuento quitado' : `Descuento ${pct.replace('.', ',')} % aplicado a ${line.name}`, {
      key: 'discount',
    });
    onClose();
  };

  const remove = () => {
    setDiscount(line.lineId, '0.00');
    toast.success('Descuento quitado', { key: 'discount' });
    onClose();
  };

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[min(26rem,94vw)] -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-surface-raised p-5 shadow-2xl">
          <Dialog.Title className="flex items-center gap-2 text-lg font-bold text-ink">
            <Percent className="size-5 text-accent" aria-hidden />
            Descuento · {line.name}
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-ink-muted">
            Porcentaje sobre la línea actual ({formatMoney(base)} € antes de descuento).
          </Dialog.Description>

          <p className="mt-4 rounded-xl bg-surface px-4 py-3 text-right text-3xl font-bold text-ink tabular-nums">
            {buffer === '' ? '—' : `${buffer.replace('.', ',')} %`}
          </p>

          {buffer !== '' && !error && (
            <p className="mt-2 text-sm text-ink-muted">
              La línea pasa de {formatMoney(base)} a{' '}
              <span className="font-bold text-accent">
                {formatMoney(lineTotalDiscounted(priceCents, line.qtyMilli, rateToContract(buffer)))} €
              </span>
            </p>
          )}
          {error && (
            <p role="alert" className="mt-2 text-sm text-danger-text">
              {error}
            </p>
          )}

          <div className="mt-3 grid grid-cols-4 gap-2">
            {PRESETS.map((preset) => (
              <button
                key={preset}
                type="button"
                onClick={() => setBuffer(preset)}
                className="flex h-11 items-center justify-center rounded-lg bg-surface-sunken text-base font-semibold text-ink transition hover:bg-surface-hover active:scale-[0.98]"
              >
                {preset} %
              </button>
            ))}
          </div>

          <Keypad onKey={onKey} className="mt-3" />

          <div className="mt-3 flex items-center justify-between gap-3">
            {line.discountPct !== '0.00' ? (
              <button
                type="button"
                onClick={remove}
                className="rounded-lg px-4 py-2.5 text-sm font-medium text-danger-text transition hover:bg-red-950/40"
              >
                Quitar descuento
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
                onClick={apply}
                disabled={buffer === '' || error !== null}
                className="rounded-lg bg-accent px-6 py-2.5 font-bold text-accent-ink transition hover:bg-accent-hover active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
              >
                Aplicar
              </button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function validate(buffer: string): string | null {
  if (buffer === '') return null;
  const cents = amountToCents(buffer);
  if (cents === null) return 'Porcentaje no válido';
  let bp: bigint;
  try {
    bp = pctToBp(buffer.replace(',', '.'));
  } catch {
    return 'El descuento debe estar entre 0 y 100';
  }
  if (bp > 10_000n) return 'El descuento no puede pasar del 100 %';
  return null;
}
