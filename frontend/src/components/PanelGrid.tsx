/**
 * Rejilla de productos del contenedor activo (panel "Principal" o un
 * subpanel). Cada botón ocupa su hueco del diseño (grid_row/grid_col); el
 * resto de celdas quedan libres. Rejilla de hasta 20×20 según arquitectura.
 */

import type { PanelItem } from '../lib/schemas';
import ProductButton from './ProductButton';

interface PanelGridProps {
  items: PanelItem[];
  onPick: (item: PanelItem) => void;
}

export default function PanelGrid({ items, onPick }: PanelGridProps) {
  if (items.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <p className="text-lg text-slate-400">
          Sin botones en esta vista. Añade productos al panel desde la API de catálogo.
        </p>
      </div>
    );
  }

  const cols = Math.max(...items.map((item) => item.grid_col)) + 1;

  return (
    <div
      role="grid"
      aria-label="Productos del panel"
      className="grid flex-1 content-start gap-2 overflow-y-auto p-4"
      style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
    >
      {items.map((item) => (
        <div key={item.id} style={{ gridColumn: item.grid_col + 1, gridRow: item.grid_row + 1 }}>
          <ProductButton item={item} onPick={onPick} />
        </div>
      ))}
    </div>
  );
}
