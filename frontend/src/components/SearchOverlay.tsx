/**
 * Búsqueda (F2): sobre la caché local, nunca contra el servidor. Una cifra
 * larga + Enter se trata como código de barras (lector o tecleado). Enter añade
 * el resultado marcado; la ventana sigue abierta para ventas seguidas.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Search, X } from 'lucide-react';
import { formatMoney, parseMoney } from '../lib/money';
import { searchProducts } from '../lib/search';
import { toCartProduct } from '../lib/product';
import type { PosProduct } from '../lib/schemas';
import { useCart } from '../state/cart';

interface SearchOverlayProps {
  products: PosProduct[];
  onClose: () => void;
}

export default function SearchOverlay({ products, onClose }: SearchOverlayProps) {
  const addProduct = useCart((state) => state.addProduct);
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState(0);
  const [miss, setMiss] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const { exactBarcode, results } = useMemo(
    () => searchProducts(products, query),
    [products, query],
  );

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    setHighlight(0);
    setMiss(null);
  }, [query]);

  const addAndContinue = (product: PosProduct) => {
    addProduct(toCartProduct(product));
    setQuery('');
  };

  const confirm = () => {
    if (exactBarcode) {
      addAndContinue(exactBarcode);
      return;
    }
    const chosen = results[highlight] ?? results[0];
    if (chosen) addAndContinue(chosen);
    else if (query.trim()) setMiss(`Sin resultados para «${query.trim()}»`);
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setHighlight((index) => Math.min(index + 1, results.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setHighlight((index) => Math.max(index - 1, 0));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      confirm();
    }
  };

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content
          className="fixed left-1/2 top-20 w-[min(42rem,92vw)] -translate-x-1/2 rounded-2xl bg-slate-800 p-5 shadow-2xl"
          onKeyDown={onKeyDown}
        >
          <Dialog.Title className="mb-3 flex items-center gap-2 text-lg font-bold text-slate-100">
            <Search className="size-5 text-amber-400" aria-hidden />
            Buscar producto
          </Dialog.Title>
          <Dialog.Description className="sr-only">
            Escribe nombre, alias o código de barras y pulsa Enter para añadir al ticket.
          </Dialog.Description>

          <div className="flex items-center gap-2">
            <input
              ref={inputRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Nombre, alias, SKU o código de barras…"
              autoComplete="off"
              className="w-full rounded-lg border border-slate-600 bg-slate-900 px-4 py-3 text-lg text-slate-100 outline-none focus:border-amber-400"
            />
            <Dialog.Close
              className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-slate-700 text-slate-300 hover:bg-slate-600"
              aria-label="Cerrar búsqueda"
            >
              <X className="size-5" aria-hidden />
            </Dialog.Close>
          </div>

          {miss && (
            <p role="alert" className="mt-2 text-sm text-red-400">
              {miss}
            </p>
          )}

          {exactBarcode && (
            <p className="mt-2 text-sm text-amber-300">
              Código leído: {exactBarcode.name} — Enter para añadirlo
            </p>
          )}

          {!exactBarcode && results.length > 0 && (
            <ul className="mt-3 max-h-80 overflow-y-auto">
              {results.map((product, index) => (
                <li key={product.id}>
                  <button
                    type="button"
                    onMouseDown={(event) => {
                      event.preventDefault(); // el input no pierde el foco
                      addAndContinue(product);
                    }}
                    onMouseEnter={() => setHighlight(index)}
                    className={`flex w-full items-center justify-between rounded-lg px-4 py-3 text-left ${
                      index === highlight ? 'bg-amber-500/90 text-slate-900' : 'bg-slate-700 text-slate-100 hover:bg-slate-600'
                    }`}
                  >
                    <span className="truncate">
                      {product.name}
                      {product.sku && <span className="ml-2 text-xs opacity-70">{product.sku}</span>}
                    </span>
                    <span className="ml-3 font-bold">{formatMoney(parseMoney(product.price))} €</span>
                  </button>
                </li>
              ))}
            </ul>
          )}

          <p className="mt-3 text-xs text-slate-400">
            ↑/↓ elige · Enter añade · Esc cierra
          </p>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
