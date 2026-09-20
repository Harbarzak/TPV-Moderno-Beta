/**
 * Pedido en mesa (fase 31 · camarero): Mesa -> categoría -> producto ->
 * modificadores/notas -> enviar. Botones grandes, una sola pantalla: el ticket
 * vive debajo de la carta y «Enviar» vuelve a Mesas.
 *
 * Honestidad de sincronización: la mesa es estado COMPARTIDO (mostrador,
 * sala, futuro KDS), así que cada toque va DIRECTO al servidor y después se
 * relee el pedido — lo visible es siempre la verdad del servidor, sin cola
 * offline (esa es del ticket de barra). Sin conexión la pantalla queda en
 * lectura: tocar productos no está disponible y el banner global lo avisa.
 *
 * Modificadores (decisión de fase Productos): el backend no tiene concepto de
 * modificadores por producto; se materializan como NOTAS de línea
 * (AddLine.notes / PATCH notes, ≤200). Pulsación larga en el producto = añadir
 * con nota. Sin chips inventadas de modificadores.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft, Minus, Plus, X } from 'lucide-react';
import { z } from 'zod';
import { apiFetch } from '../lib/api';
import { useConnectivity } from '../lib/connectivity';
import { formatMoney } from '../lib/money';
import { formatQty, toQty } from '../lib/qty';
import {
  orderDetailSchema,
  posCatalogSchema,
  type OrderDetail,
  type OrderLine,
  type PosProduct,
} from '../lib/schemas';
import { EmptyNote, ErrorBox, Spinner } from '../components/Feedback';
import Keypad from '../components/Keypad';

// GET /catalog/categories responde un array pelado de CategoryResponse.
const categorySchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
  sort_order: z.number().int(),
  active: z.boolean(),
});
const categoriesSchema = z.array(categorySchema);
type Category = z.infer<typeof categorySchema>;

type Sheet =
  | { kind: 'qty'; product: PosProduct } // añadir producto pesado
  | { kind: 'line-qty'; line: OrderLine } // rectificar cantidad pesada
  | { kind: 'note'; product: PosProduct } // añadir con nota (pulsación larga)
  | { kind: 'line-note'; line: OrderLine }; // editar/quitar nota de línea

const NOTE_MAX = 200;

export default function TableOrderScreen({
  orderId,
  label,
  onBack,
}: {
  orderId: string;
  label: string;
  onBack: () => void;
}) {
  const online = useConnectivity((state) => state.online);
  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [categories, setCategories] = useState<Category[] | null>(null);
  const [products, setProducts] = useState<PosProduct[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [qtyRaw, setQtyRaw] = useState('1');
  const [noteRaw, setNoteRaw] = useState('');
  const [activeCategory, setActiveCategory] = useState<string | null>(null);

  // Pulsación larga (500 ms) = añadir con nota; suprime el click que sigue.
  const pressTimer = useRef<number | null>(null);
  const suppressClick = useRef(false);

  const reloadOrder = useCallback(async () => {
    const data = orderDetailSchema.parse(await apiFetch<unknown>(`/api/v1/sales/orders/${orderId}`));
    setOrder(data);
  }, [orderId]);

  const [loadTick, setLoadTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Categorías: array pelado; catálogo POS: {products}.
        const [cats, catalog] = await Promise.all([
          categoriesSchema.parse(await apiFetch<unknown>('/api/v1/catalog/categories')),
          posCatalogSchema.parse(await apiFetch<unknown>('/api/v1/catalog/pos')),
        ]);
        const detail = orderDetailSchema.parse(
          await apiFetch<unknown>(`/api/v1/sales/orders/${orderId}`),
        );
        if (cancelled) return;
        setCategories(cats);
        setProducts(catalog.products);
        setOrder(detail);
      } catch (cause: unknown) {
        if (cancelled) return;
        setError(cause instanceof Error ? cause.message : 'No se pudo abrir la mesa.');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orderId, loadTick]);

  const sortedCategories = useMemo(
    () => (categories ?? []).filter((cat) => cat.active).sort((a, b) => a.sort_order - b.sort_order),
    [categories],
  );

  const visibleProducts = useMemo(() => {
    if (products === null) return [];
    const inCategory = activeCategory === null
      ? products
      : products.filter((product) => product.category_id === activeCategory);
    return [...inCategory].sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name));
  }, [products, activeCategory]);

  const total = useMemo(() => {
    if (order === null) return null;
    return order.lines.reduce((acc, line) => acc + Number(line.total), 0);
  }, [order]);

  /** Toda mutación: servidor -> relectura del pedido. Sin encolar. */
  const mutate = useCallback(
    async (action: () => Promise<unknown>) => {
      if (busy || !online) return;
      setBusy(true);
      setError(null);
      try {
        await action();
        await reloadOrder();
      } catch (cause: unknown) {
        setError(cause instanceof Error ? cause.message : 'El servidor rechazó el cambio.');
      } finally {
        setBusy(false);
      }
    },
    [busy, online, reloadOrder],
  );

  const addLine = (product: PosProduct, quantity: string, notes?: string) =>
    mutate(async () => {
      const body: Record<string, string> = { product_id: product.id, quantity };
      if (notes !== undefined && notes.trim() !== '') body.notes = notes.trim();
      // Idempotency-Key por toque: un reintento de red no duplica la línea.
      await apiFetch(`/api/v1/sales/orders/${orderId}/lines`, {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify(body),
      });
      setSheet(null);
    });

  const patchLine = (line: OrderLine, patch: Record<string, string | null>) =>
    mutate(async () => {
      await apiFetch(`/api/v1/sales/orders/${orderId}/lines/${line.id}`, {
        method: 'PATCH',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify(patch),
      });
      setSheet(null);
    });

  const deleteLine = (line: OrderLine) =>
    mutate(async () => {
      await apiFetch(`/api/v1/sales/orders/${orderId}/lines/${line.id}`, { method: 'DELETE' });
      setSheet(null);
    });

  /** Un toque: pesados abren cantidad; el resto añade unidad directa. */
  const tapProduct = (product: PosProduct) => {
    if (suppressClick.current) {
      suppressClick.current = false;
      return;
    }
    setError(null);
    if (product.weighable) {
      setQtyRaw('1');
      setSheet({ kind: 'qty', product });
    } else {
      void addLine(product, '1.000');
    }
  };

  const startPress = (product: PosProduct) => {
    pressTimer.current = window.setTimeout(() => {
      pressTimer.current = null;
      suppressClick.current = true;
      setError(null);
      setNoteRaw('');
      setSheet({ kind: 'note', product });
    }, 500);
  };
  const cancelPress = () => {
    if (pressTimer.current !== null) {
      clearTimeout(pressTimer.current);
      pressTimer.current = null;
    }
  };

  const openLineQty = (line: OrderLine) => {
    setQtyRaw(line.quantity.replace('.', ','));
    setSheet({ kind: 'line-qty', line });
  };
  const openLineNote = (line: OrderLine) => {
    setNoteRaw(line.notes ?? '');
    setSheet({ kind: 'line-note', line });
  };

  const qtyValid = toQty(qtyRaw) !== null;

  if (order === null || categories === null || products === null) {
    return (
      <div className="flex h-full flex-col">
        {error && <ErrorBox message={error} onRetry={() => setLoadTick((tick) => tick + 1)} />}
        <Spinner />
      </div>
    );
  }

  /** La línea es pesable si su producto sigue existiendo en la carta. */
  const isWeighableLine = (line: OrderLine): boolean =>
    line.product_id !== null && products.some((p) => p.id === line.product_id && p.weighable);

  const guests = order.guest_count !== null ? ` · ${order.guest_count} comensales` : '';

  return (
    <div className="flex h-full flex-col">
      {/* Cabecera: volver a Mesas y contexto del pedido */}
      <div className="flex items-center gap-1 border-b border-slate-200 bg-white px-2 py-2">
        <button
          type="button"
          onClick={onBack}
          aria-label="Volver a mesas"
          className="flex h-10 w-10 items-center justify-center rounded-xl text-slate-600 active:bg-slate-100"
        >
          <ChevronLeft size={24} aria-hidden />
        </button>
        <div className="min-w-0">
          <p className="truncate text-base font-bold text-slate-800">
            {label}
            {guests}
          </p>
          <p className="text-[11px] text-slate-500">Pedido de sala · se guarda al tocar</p>
        </div>
      </div>

      {error && <ErrorBox message={error} />}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* Categorías: chips horizontales */}
        <div className="sticky top-0 z-10 flex gap-2 overflow-x-auto border-b border-slate-100 bg-white/95 px-3 py-2 backdrop-blur">
          <button
            type="button"
            onClick={() => setActiveCategory(null)}
            className={`shrink-0 rounded-full px-3 py-1.5 text-sm font-semibold ${
              activeCategory === null ? 'bg-teal-700 text-white' : 'bg-slate-100 text-slate-700'
            }`}
          >
            Todo
          </button>
          {sortedCategories.map((cat) => (
            <button
              key={cat.id}
              type="button"
              onClick={() => setActiveCategory(cat.id)}
              className={`shrink-0 rounded-full px-3 py-1.5 text-sm font-semibold ${
                activeCategory === cat.id ? 'bg-teal-700 text-white' : 'bg-slate-100 text-slate-700'
              }`}
            >
              {cat.name}
            </button>
          ))}
        </div>

        {/* Carta: botones grandes; toque añade, pulsación larga añade con nota */}
        <div className="grid grid-cols-2 gap-2 p-3">
          {visibleProducts.map((product) => (
            <button
              key={product.id}
              type="button"
              disabled={busy || !online}
              onPointerDown={() => startPress(product)}
              onPointerUp={cancelPress}
              onPointerLeave={cancelPress}
              onContextMenu={(event) => event.preventDefault()}
              onClick={() => tapProduct(product)}
              className="card select-none p-3 text-left transition active:scale-[0.98] disabled:opacity-40"
            >
              <span className="line-clamp-2 text-sm font-bold text-slate-800">{product.name}</span>
              <span className="mt-1 flex items-center justify-between">
                <span className="text-sm font-semibold tabular-nums text-teal-700">
                  {formatMoney(product.price)}
                </span>
                {product.weighable && (
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">
                    kg
                  </span>
                )}
              </span>
            </button>
          ))}
          {visibleProducts.length === 0 && (
            <p className="col-span-2">
              <EmptyNote>No hay productos en esta categoría.</EmptyNote>
            </p>
          )}
        </div>

        {/* Ticket del servidor: rectificable línea a línea */}
        <section className="border-t border-slate-200 bg-slate-50 px-3 py-3" aria-label="Pedido">
          <h2 className="mb-2 text-xs font-bold uppercase tracking-wide text-slate-500">
            Pedido ({order.lines.length})
          </h2>
          {order.lines.length === 0 ? (
            <EmptyNote>Toca productos para añadirlos al pedido.</EmptyNote>
          ) : (
            <ul className="flex flex-col gap-2">
              {order.lines.map((line) => (
                <li key={line.id} className="card flex flex-col gap-1 p-2.5">
                  <div className="flex items-start justify-between gap-2">
                    <span className="min-w-0 flex-1 text-sm font-semibold text-slate-800">
                      {line.name}
                    </span>
                    <span className="text-sm font-bold tabular-nums text-slate-800">
                      {formatMoney(line.total)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {line.notes !== null && (
                      <button
                        type="button"
                        onClick={() => openLineNote(line)}
                        disabled={busy || !online}
                        className="max-w-[45%] truncate rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-900 active:bg-amber-200 disabled:opacity-40"
                      >
                        {line.notes}
                      </button>
                    )}
                    <span className="flex-1" />
                    {isWeighableLine(line) ? (
                      <button
                        type="button"
                        onClick={() => openLineQty(line)}
                        disabled={busy || !online}
                        className="flex h-8 min-w-14 items-center justify-center rounded-lg bg-slate-100 px-2 text-sm font-bold tabular-nums text-slate-800 active:bg-slate-200 disabled:opacity-40"
                        aria-label={`Cambiar cantidad de ${line.name}`}
                      >
                        {formatQty(line.quantity)} kg
                      </button>
                    ) : (
                      <span className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => {
                            const next = Number(line.quantity) - 1;
                            if (next <= 0) void deleteLine(line);
                            else void patchLine(line, { quantity: next.toFixed(3) });
                          }}
                          disabled={busy || !online}
                          aria-label={`Quitar una unidad de ${line.name}`}
                          className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100 text-slate-700 active:bg-slate-200 disabled:opacity-40"
                        >
                          <Minus size={16} aria-hidden />
                        </button>
                        <span className="min-w-8 text-center text-sm font-bold tabular-nums text-slate-800">
                          {formatQty(line.quantity)}
                        </span>
                        <button
                          type="button"
                          onClick={() => void patchLine(line, { quantity: (Number(line.quantity) + 1).toFixed(3) })}
                          disabled={busy || !online}
                          aria-label={`Añadir una unidad de ${line.name}`}
                          className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100 text-slate-700 active:bg-slate-200 disabled:opacity-40"
                        >
                          <Plus size={16} aria-hidden />
                        </button>
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => void deleteLine(line)}
                      disabled={busy || !online}
                      aria-label={`Quitar ${line.name} del pedido`}
                      className="flex h-8 w-8 items-center justify-center rounded-lg bg-rose-50 text-rose-600 active:bg-rose-100 disabled:opacity-40"
                    >
                      <X size={16} aria-hidden />
                    </button>
                  </div>
                  {line.notes === null && (
                    <button
                      type="button"
                      onClick={() => openLineNote(line)}
                      disabled={busy || !online}
                      className="self-start text-[11px] font-semibold text-teal-700 active:text-teal-800 disabled:opacity-40"
                    >
                      + nota
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      {/* Pie: total y Enviar (todo ya está en el servidor; Enviar vuelve a Mesas) */}
      <div className="border-t border-slate-200 bg-white p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm font-semibold text-slate-600">Total</span>
          <span className="text-lg font-bold tabular-nums text-slate-800">
            {formatMoney((total ?? 0).toFixed(2))}
          </span>
        </div>
        <button
          type="button"
          onClick={onBack}
          disabled={busy}
          className="btn-primary w-full"
        >
          Enviar
        </button>
        {!online && (
          <p className="mt-1 text-center text-[11px] text-amber-800">
            Sin conexión: solo lectura hasta que vuelva la red.
          </p>
        )}
      </div>

      {/* Hojas inferiores: cantidad pesada y notas */}
      {sheet !== null && (
        <div
          className="fixed inset-0 z-40 flex flex-col justify-end bg-slate-900/40"
          onClick={() => setSheet(null)}
        >
          <div
            className="mx-auto w-full max-w-md rounded-t-2xl bg-white p-4"
            onClick={(event) => event.stopPropagation()}
          >
            {sheet.kind === 'qty' || sheet.kind === 'line-qty' ? (
              <>
                <h3 className="mb-1 text-base font-bold text-slate-800">
                  {sheet.kind === 'qty' ? sheet.product.name : sheet.line.name}
                </h3>
                <p className="mb-2 text-xs text-slate-500">Cantidad en kilos</p>
                <p className="mb-3 text-center text-3xl font-bold tabular-nums text-slate-800">
                  {qtyRaw || '0'} kg
                </p>
                <Keypad
                  onDigit={(digit) =>
                    setQtyRaw((raw) => (/^\d{1,4}(,\d{0,2})?$/.test(raw + digit) ? raw + digit : raw))
                  }
                  onDot={() => setQtyRaw((raw) => (raw.includes(',') ? raw : `${raw},`))}
                  onBackspace={() => setQtyRaw((raw) => raw.slice(0, -1))}
                />
                <div className="mt-3 flex gap-2">
                  <button type="button" className="btn-secondary flex-1" onClick={() => setSheet(null)}>
                    Cancelar
                  </button>
                  <button
                    type="button"
                    className="btn-primary flex-1"
                    disabled={!qtyValid || busy}
                    onClick={() => {
                      const qty = toQty(qtyRaw);
                      if (qty === null) return;
                      if (sheet.kind === 'qty') void addLine(sheet.product, qty);
                      else void patchLine(sheet.line, { quantity: qty });
                    }}
                  >
                    {sheet.kind === 'qty' ? 'Añadir' : 'Guardar'}
                  </button>
                </div>
              </>
            ) : (
              <>
                <h3 className="mb-1 text-base font-bold text-slate-800">
                  {sheet.kind === 'note' ? `Nota · ${sheet.product.name}` : `Nota · ${sheet.line.name}`}
                </h3>
                <p className="mb-2 text-xs text-slate-500">
                  Modificadores, punto de carne, alergias… (máx. {NOTE_MAX} caracteres)
                </p>
                <textarea
                  value={noteRaw}
                  onChange={(event) => setNoteRaw(event.target.value.slice(0, NOTE_MAX))}
                  rows={3}
                  maxLength={NOTE_MAX}
                  className="input-base resize-none"
                  placeholder="Sin cebolla, poco hecho…"
                />
                <div className="mt-3 flex gap-2">
                  <button type="button" className="btn-secondary flex-1" onClick={() => setSheet(null)}>
                    Cancelar
                  </button>
                  {sheet.kind === 'note' ? (
                    <button
                      type="button"
                      className="btn-primary flex-1"
                      disabled={busy}
                      onClick={() => void addLine(sheet.product, '1.000', noteRaw)}
                    >
                      Añadir
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn-primary flex-1"
                      disabled={busy}
                      onClick={() =>
                        void patchLine(sheet.line, {
                          notes: noteRaw.trim() === '' ? null : noteRaw.trim(),
                        })
                      }
                    >
                      Guardar
                    </button>
                  )}
                </div>
                {sheet.kind === 'line-note' && sheet.line.notes !== null && (
                  <button
                    type="button"
                    className="btn-secondary mt-2 w-full"
                    disabled={busy}
                    onClick={() => void patchLine(sheet.line, { notes: null })}
                  >
                    Quitar la nota
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
