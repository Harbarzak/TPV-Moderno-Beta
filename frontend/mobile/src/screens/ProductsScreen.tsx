/**
 * Consulta de productos (solo lectura): nombre, SKU y precio del servidor.
 * Sin edición — el catálogo se gestiona en administración (fases previas/21).
 */

import { useEffect, useState } from 'react';
import { Search } from 'lucide-react';
import { apiFetch } from '../lib/api';
import { formatMoney } from '../lib/money';
import { productListSchema, type Product } from '../lib/schemas';
import Screen from '../components/Screen';
import { EmptyNote, ErrorBox, Spinner } from '../components/Feedback';

const SEARCH_DELAY_MS = 400;

export default function ProductsScreen() {
  const [search, setSearch] = useState('');
  const [products, setProducts] = useState<Product[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setLoading(true);
      setError(null);
      const query = new URLSearchParams({ limit: '30' });
      if (search.trim() !== '') query.set('search', search.trim());
      apiFetch<unknown>(`/api/v1/catalog/products?${query.toString()}`, {
        signal: controller.signal,
      })
        .then((raw) => setProducts(productListSchema.parse(raw).items))
        .catch((cause: unknown) => {
          if (cause instanceof Error && cause.name === 'AbortError') return;
          setError(cause instanceof Error ? cause.message : 'No se pudieron cargar los productos.');
        })
        .finally(() => setLoading(false));
    }, SEARCH_DELAY_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [search]);

  return (
    <Screen title="Productos">
      <div className="relative mb-3">
        <Search size={18} aria-hidden className="absolute left-3 top-3 text-slate-400" />
        <input
          className="input-base pl-9"
          placeholder="Buscar por nombre o SKU…"
          value={search}
          autoCapitalize="none"
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>

      {error && <ErrorBox message={error} />}
      {loading && products === null ? (
        <Spinner />
      ) : products === null ? null : products.length === 0 ? (
        <EmptyNote>Sin resultados.</EmptyNote>
      ) : (
        <ul className="divide-y divide-slate-100 rounded-2xl border border-slate-200 bg-white">
          {products.map((product) => (
            <li key={product.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-800">{product.name}</p>
                {product.sku && <p className="text-xs text-slate-500">SKU {product.sku}</p>}
              </div>
              <p className="shrink-0 text-sm font-semibold text-teal-700">
                {formatMoney(product.price)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Screen>
  );
}
