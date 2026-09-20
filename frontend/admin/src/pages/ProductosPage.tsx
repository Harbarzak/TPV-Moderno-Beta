/**
 * Productos (/catalog/products): búsqueda y filtros, alta, edición y baja
 * lógica. El precio viaja SIEMPRE como string decimal (§3); el IVA es
 * obligatorio (el backend lo exige para calcular documentos).
 */

import { useState } from 'react';
import { apiFetch } from '../lib/api';
import {
  categorySchema,
  productPageSchema,
  taxRateSchema,
  type Category,
  type Product,
  type TaxRate,
} from '../lib/domain';
import {
  Badge,
  Banner,
  Empty,
  Field,
  Loading,
  Modal,
  PageHeader,
  useAction,
  useAsync,
} from '../components/ui';

const eur = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' });
const money = (price: string): string => {
  const value = Number.parseFloat(price);
  return Number.isNaN(value) ? price : eur.format(value);
};

export default function ProductosPage() {
  const [search, setSearch] = useState('');
  const [categoryId, setCategoryId] = useState('');
  const [includeInactive, setIncludeInactive] = useState(false);

  const { data, error, loading, reload } = useAsync(() => {
    const params = new URLSearchParams({ limit: '100' });
    if (search.trim()) params.set('search', search.trim());
    if (categoryId) params.set('category_id', categoryId);
    if (!includeInactive) params.set('active', 'true');
    return apiFetch<unknown>(`/api/v1/catalog/products?${params.toString()}`).then(productPageSchema.parse);
  }, [search, categoryId, includeInactive]);

  const { data: categories } = useAsync(
    () =>
      apiFetch<unknown>('/api/v1/catalog/categories?include_inactive=true')
        .then(categorySchema.array().parse),
    [],
  );
  const { data: taxRates } = useAsync(
    () => apiFetch<unknown>('/api/v1/catalog/tax-rates').then(taxRateSchema.array().parse),
    [],
  );

  const action = useAction();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Product | null>(null);

  const categoryName = (id: string | null): string =>
    id ? (categories?.find((c) => c.id === id)?.name ?? '—') : '—';
  const taxLabel = (id: string): string => {
    const rate = taxRates?.find((t) => t.id === id);
    return rate ? `${rate.code} ${rate.rate}%` : '—';
  };

  const deactivate = (product: Product) => {
    void action.run(async () => {
      await apiFetch(`/api/v1/catalog/products/${product.id}`, { method: 'DELETE' });
      reload();
    });
  };

  return (
    <>
      <PageHeader
        title="Productos"
        hint="Catálogo que vende el TPV. La baja es lógica: el histórico se conserva."
        actions={
          <button type="button" className="btn-primary" onClick={() => setCreating(true)}>
            Nuevo producto
          </button>
        }
      />
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}

      <div className="card mb-4 grid gap-3 sm:grid-cols-[2fr_2fr_auto_auto] sm:items-end">
        <Field label="Buscar">
          <input
            className="input-base"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            maxLength={120}
            placeholder="Nombre o SKU…"
          />
        </Field>
        <Field label="Categoría">
          <select className="input-base" value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
            <option value="">Todas</option>
            {(categories ?? []).map((category) => (
              <option key={category.id} value={category.id}>
                {category.name}
              </option>
            ))}
          </select>
        </Field>
        <label className="flex items-center gap-2 pb-2.5 text-sm">
          <input
            type="checkbox"
            checked={includeInactive}
            onChange={(e) => setIncludeInactive(e.target.checked)}
          />
          Incluir dados de baja
        </label>
        <p className="pb-2.5 text-xs text-slate-400">{data ? `${data.total} resultado(s)` : ''}</p>
      </div>

      <div className="card overflow-x-auto p-0">
        {loading ? (
          <Loading />
        ) : error ? (
          <Banner>{error}</Banner>
        ) : !data || data.items.length === 0 ? (
          <Empty>Sin productos para estos filtros.</Empty>
        ) : (
          <table className="table-base">
            <thead>
              <tr>
                <th>Nombre</th>
                <th>SKU</th>
                <th>Categoría</th>
                <th>Precio</th>
                <th>IVA</th>
                <th>Estado</th>
                <th aria-label="Acciones" />
              </tr>
            </thead>
            <tbody>
              {data.items.map((product) => (
                <tr key={product.id}>
                  <td className="font-medium">
                    {product.name}
                    {product.weighable && (
                      <span className="ml-1 text-[10px] uppercase text-slate-400">peso</span>
                    )}
                  </td>
                  <td className="font-mono text-xs">{product.sku ?? '—'}</td>
                  <td>{categoryName(product.category_id)}</td>
                  <td className="tabular-nums">{money(product.price)}</td>
                  <td>{taxLabel(product.tax_rate_id)}</td>
                  <td>
                    <Badge ok={product.active} />
                  </td>
                  <td className="whitespace-nowrap text-right">
                    <button type="button" className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setEditing(product)}>
                      Editar
                    </button>{' '}
                    {product.active && (
                      <button
                        type="button"
                        className="btn-danger !px-2 !py-1 text-xs"
                        onClick={() => {
                          if (window.confirm(`¿Dar de baja «${product.name}»?`)) deactivate(product);
                        }}
                      >
                        Dar de baja
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {creating && (
        <ProductFormModal
          product={null}
          taxRates={taxRates ?? []}
          categories={categories ?? []}
          onClose={() => setCreating(false)}
          onSaved={() => {
            setCreating(false);
            reload();
          }}
        />
      )}
      {editing && (
        <ProductFormModal
          product={editing}
          taxRates={taxRates ?? []}
          categories={categories ?? []}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            reload();
          }}
        />
      )}
    </>
  );
}

function ProductFormModal({ product, taxRates, categories, onClose, onSaved }: {
  product: Product | null;
  taxRates: TaxRate[];
  categories: Category[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const action = useAction();
  const [name, setName] = useState(product?.name ?? '');
  const [shortName, setShortName] = useState(product?.short_name ?? '');
  const [sku, setSku] = useState(product?.sku ?? '');
  const [price, setPrice] = useState(product?.price ?? '');
  const [taxRateId, setTaxRateId] = useState(product?.tax_rate_id ?? '');
  const [categoryId, setCategoryId] = useState(product?.category_id ?? '');
  const [barcodes, setBarcodes] = useState('');
  const [weighable, setWeighable] = useState(product?.weighable ?? false);
  const [kitchen, setKitchen] = useState(product?.kitchen ?? false);
  const [active, setActive] = useState(product?.active ?? true);

  const priceValid = /^\d+(\.\d{1,2})?$/.test(price);

  const save = () => {
    if (!name.trim() || !priceValid || !taxRateId) return;
    void action.run(async () => {
      const common = {
        name: name.trim(),
        short_name: shortName.trim() || undefined,
        price,
        tax_rate_id: taxRateId,
        category_id: categoryId || null,
        weighable,
        kitchen,
      };
      if (product === null) {
        const barcodeList = barcodes
          .split(',')
          .map((code) => code.trim())
          .filter((code) => code.length > 0);
        await apiFetch('/api/v1/catalog/products', {
          method: 'POST',
          body: JSON.stringify({
            ...common,
            sku: sku.trim() || undefined,
            active: true,
            ...(barcodeList.length > 0 ? { barcodes: barcodeList } : {}),
          }),
        });
      } else {
        await apiFetch(`/api/v1/catalog/products/${product.id}`, {
          method: 'PATCH',
          body: JSON.stringify({
            ...common,
            active,
            ...(sku.trim() ? { sku: sku.trim() } : {}),
          }),
        });
      }
      onSaved();
    });
  };

  return (
    <Modal
      title={product === null ? 'Nuevo producto' : `Editar «${product.name}»`}
      onClose={onClose}
      wide
    >
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Nombre">
          <input className="input-base" value={name} onChange={(e) => setName(e.target.value)} maxLength={160} />
        </Field>
        <Field label="Nombre corto (panel)">
          <input className="input-base" value={shortName} onChange={(e) => setShortName(e.target.value)} maxLength={40} />
        </Field>
        <Field label="SKU">
          <input className="input-base" value={sku} onChange={(e) => setSku(e.target.value)} maxLength={64} />
        </Field>
        <Field label="Precio (€)">
          <input
            className="input-base"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            inputMode="decimal"
            pattern="\d+(\.\d{1,2})?"
            placeholder="1.50"
          />
        </Field>
        <Field label="Tipo de IVA">
          <select className="input-base" value={taxRateId} onChange={(e) => setTaxRateId(e.target.value)} required>
            <option value="" disabled>
              Elige…
            </option>
            {taxRates.map((rate) => (
              <option key={rate.id} value={rate.id}>
                {rate.code} · {rate.rate}%
              </option>
            ))}
          </select>
        </Field>
        <Field label="Categoría">
          <select className="input-base" value={categoryId} onChange={(e) => setCategoryId(e.target.value)}>
            <option value="">— Sin categoría —</option>
            {categories.map((category) => (
              <option key={category.id} value={category.id}>
                {category.name}
              </option>
            ))}
          </select>
        </Field>
        {product === null && (
          <div className="sm:col-span-2">
            <Field label="Códigos de barras (separados por comas)">
              <input
                className="input-base"
                value={barcodes}
                onChange={(e) => setBarcodes(e.target.value)}
                placeholder="8412345678901, 8412345678902"
              />
            </Field>
          </div>
        )}
      </div>
      <div className="mt-3 flex flex-wrap gap-4 text-sm">
        <label className="flex items-center gap-2" title="Se vende por peso (báscula)">
          <input type="checkbox" checked={weighable} onChange={(e) => setWeighable(e.target.checked)} />
          Pesable
        </label>
        <label className="flex items-center gap-2" title="Envía comanda a la impresora de cocina">
          <input type="checkbox" checked={kitchen} onChange={(e) => setKitchen(e.target.checked)} />
          Va a cocina
        </label>
        {product !== null && (
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
            Activo
          </label>
        )}
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className="btn-secondary" onClick={onClose}>
          Cancelar
        </button>
        <button
          type="button"
          className="btn-primary"
          onClick={save}
          disabled={action.busy || !name.trim() || !priceValid || !taxRateId}
        >
          {product === null ? 'Crear' : 'Guardar'}
        </button>
      </div>
    </Modal>
  );
}
