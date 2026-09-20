/**
 * Botón de producto de la rejilla: grande (táctil), con la alternativa del
 * diseño (label/color) si existe y el precio del snapshot siempre visible.
 */

import type { PanelItem } from '../lib/schemas';
import { formatMoney, parseMoney } from '../lib/money';

interface ProductButtonProps {
  item: PanelItem;
  onPick: (item: PanelItem) => void;
}

export default function ProductButton({ item, onPick }: ProductButtonProps) {
  const { product } = item;
  const title = item.label ?? product.short_name ?? product.name;

  return (
    <button
      type="button"
      onClick={() => onPick(item)}
      style={item.color ? { backgroundColor: item.color } : undefined}
      className={
        'flex min-h-24 min-w-0 flex-col items-start justify-between rounded-xl p-3 text-left ' +
        'shadow transition active:scale-[0.97] ' +
        (item.color ? 'text-white drop-shadow' : 'bg-slate-700 text-slate-100 hover:bg-slate-600')
      }
    >
      <span className="line-clamp-3 w-full text-base font-semibold leading-tight" title={title}>
        {title}
      </span>
      <span className="mt-2 text-sm font-bold opacity-90">
        {formatMoney(parseMoney(product.price))} €
      </span>
    </button>
  );
}
