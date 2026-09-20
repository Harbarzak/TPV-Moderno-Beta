/**
 * Paneles (/catalog/panels): la rejilla de botones que usa el TPV.
 * Paneles → subpaneles → items (producto + posición 0-19). El PUT de
 * items reemplaza la lista completa: siempre se reenvían los existentes.
 */

import { useState, type FormEvent } from 'react';
import { apiFetch } from '../lib/api';
import {
  panelTreeSchema,
  productPageSchema,
  type Panel,
  type PanelItem,
  type Product,
  type SubPanel,
} from '../lib/domain';
import {
  Banner,
  Empty,
  Field,
  Loading,
  Modal,
  PageHeader,
  useAction,
  useAsync,
} from '../components/ui';

const MAX_ITEMS = 400;

type PlacedItem = { item: PanelItem; subpanel: SubPanel | null };

const placedOf = (panel: Panel): PlacedItem[] => [
  ...panel.items.map((item) => ({ item, subpanel: null })),
  ...panel.subpanels.flatMap((subpanel) =>
    subpanel.items.map((item) => ({ item, subpanel })),
  ),
];

export default function PanelesPage() {
  const { data: tree, error, loading, reload } = useAsync(
    () => apiFetch<unknown>('/api/v1/catalog/panels').then(panelTreeSchema.parse),
    [],
  );
  const { data: products } = useAsync(
    () =>
      apiFetch<unknown>('/api/v1/catalog/products?active=true&limit=200')
        .then(productPageSchema.parse),
    [],
  );

  const action = useAction();
  const [panelId, setPanelId] = useState('');
  const [editPanel, setEditPanel] = useState<Panel | null>(null);
  const [renaming, setRenaming] = useState<SubPanel | null>(null);

  const panels = tree?.panels ?? [];
  const panel = panels.find((item) => item.id === panelId) ?? panels[0] ?? null;

  const submitPanel = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    void action.run(async () => {
      const created = await apiFetch<unknown>('/api/v1/catalog/panels', {
        method: 'POST',
        body: JSON.stringify({
          name: String(form.get('name') ?? '').trim(),
          sort_order: Number(form.get('sort_order') ?? 0),
        }),
      });
      formEl.reset();
      reload();
      setPanelId((created as { id: string }).id);
    });
  };

  const deletePanel = (target: Panel) => {
    if (!window.confirm(`¿Dar de baja el panel «${target.name}»? Desaparecerá de la venta.`)) return;
    void action.run(async () => {
      await apiFetch(`/api/v1/catalog/panels/${target.id}`, { method: 'DELETE' });
      setPanelId('');
      reload();
    });
  };

  const submitSubpanel = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!panel) return;
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    void action.run(async () => {
      await apiFetch(`/api/v1/catalog/panels/${panel.id}/subpanels`, {
        method: 'POST',
        body: JSON.stringify({
          name: String(form.get('name') ?? '').trim(),
          sort_order: Number(form.get('sort_order') ?? 0),
        }),
      });
      formEl.reset();
      reload();
    });
  };

  const deleteSubpanel = (subpanel: SubPanel) => {
    if (!window.confirm(`¿Eliminar el subpanel «${subpanel.name}»? Sus items también desaparecen.`)) return;
    void action.run(async () => {
      await apiFetch(`/api/v1/catalog/subpanels/${subpanel.id}`, { method: 'DELETE' });
      reload();
    });
  };

  return (
    <>
      <PageHeader
        title="Paneles"
        hint="Rejillas de botones de la pantalla de venta: paneles, subpaneles y productos."
      />
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}

      <form onSubmit={submitPanel} className="card mb-4 grid gap-3 sm:grid-cols-[2fr_100px_auto] sm:items-end">
        <Field label="Nuevo panel">
          <input name="name" className="input-base" required maxLength={80} placeholder="Bebidas" />
        </Field>
        <Field label="Orden">
          <input name="sort_order" type="number" className="input-base" defaultValue={0} />
        </Field>
        <button type="submit" className="btn-primary" disabled={action.busy}>
          Crear panel
        </button>
      </form>

      {loading ? (
        <div className="card">
          <Loading />
        </div>
      ) : error ? (
        <Banner>{error}</Banner>
      ) : panels.length === 0 ? (
        <Empty>Todavía no hay paneles. Crea el primero arriba.</Empty>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap gap-2">
            {panels.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setPanelId(item.id)}
                className={
                  panel?.id === item.id
                    ? 'btn-primary !px-3 !py-1.5 text-xs'
                    : 'btn-secondary !px-3 !py-1.5 text-xs'
                }
              >
                {item.name}
              </button>
            ))}
          </div>

          {panel && (
            <div className="grid gap-4 lg:grid-cols-[1fr_2fr]">
              <div>
                <div className="card mb-4">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <h2 className="text-sm font-bold uppercase tracking-wider text-slate-400">
                      Panel «{panel.name}»
                    </h2>
                    <div className="flex gap-2">
                      <button type="button" className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setEditPanel(panel)}>
                        Editar
                      </button>
                      <button type="button" className="btn-danger !px-2 !py-1 text-xs" onClick={() => deletePanel(panel)}>
                        Dar de baja
                      </button>
                    </div>
                  </div>
                  <p className="text-xs text-slate-400">
                    {placedOf(panel).length} item(s) en {panel.subpanels.length} subpanel(es).
                  </p>
                </div>

                <div className="card">
                  <h2 className="mb-2 text-sm font-bold uppercase tracking-wider text-slate-400">Subpaneles</h2>
                  {panel.subpanels.length === 0 && (
                    <p className="mb-2 text-xs text-slate-400">Sin subpaneles: todos los items van directo al panel.</p>
                  )}
                  <ul className="mb-3">
                    {panel.subpanels.map((subpanel) => (
                      <li key={subpanel.id} className="flex items-center justify-between gap-2 py-1 text-sm">
                        <span>
                          {subpanel.name}{' '}
                          <span className="text-xs text-slate-400">({subpanel.items.length})</span>
                        </span>
                        <span className="flex gap-1">
                          <button
                            type="button"
                            className="btn-secondary !px-2 !py-0.5 text-xs"
                            onClick={() => setRenaming(subpanel)}
                          >
                            Renombrar
                          </button>
                          <button
                            type="button"
                            className="btn-danger !px-2 !py-0.5 text-xs"
                            onClick={() => deleteSubpanel(subpanel)}
                          >
                            ✕
                          </button>
                        </span>
                      </li>
                    ))}
                  </ul>
                  <form onSubmit={submitSubpanel} className="grid gap-2 sm:grid-cols-[2fr_80px_auto] sm:items-end">
                    <Field label="Nuevo subpanel">
                      <input name="name" className="input-base" required maxLength={80} placeholder="Cafés" />
                    </Field>
                    <Field label="Orden">
                      <input name="sort_order" type="number" className="input-base" defaultValue={0} />
                    </Field>
                    <button type="submit" className="btn-secondary" disabled={action.busy}>
                      Añadir
                    </button>
                  </form>
                </div>
              </div>

              <div className="card overflow-x-auto p-0">
                <ItemsCard
                  panel={panel}
                  products={products?.items ?? []}
                  onChanged={reload}
                />
              </div>
            </div>
          )}
        </>
      )}

      {editPanel && (
        <EditPanelModal
          panel={editPanel}
          onClose={() => setEditPanel(null)}
          onSaved={() => {
            setEditPanel(null);
            reload();
          }}
        />
      )}
      {renaming && (
        <EditSubpanelModal
          subpanel={renaming}
          onClose={() => setRenaming(null)}
          onSaved={() => {
            setRenaming(null);
            reload();
          }}
        />
      )}
    </>
  );
}

function ItemsCard({ panel, products, onChanged }: {
  panel: Panel;
  products: Product[];
  onChanged: () => void;
}) {
  const action = useAction();
  const [productId, setProductId] = useState('');
  const [subpanelId, setSubpanelId] = useState('');
  const [label, setLabel] = useState('');
  const [color, setColor] = useState('');
  const [gridRow, setGridRow] = useState('0');
  const [gridCol, setGridCol] = useState('0');

  const placed = placedOf(panel);
  const usedIds = new Set(placed.map(({ item }) => item.product.id));
  const available = products.filter((product) => !usedIds.has(product.id));
  const full = placed.length >= MAX_ITEMS;

  const removeItem = (item: PanelItem) => {
    void action.run(async () => {
      await apiFetch(`/api/v1/catalog/panel-items/${item.id}`, { method: 'DELETE' });
      onChanged();
    });
  };

  const clampCell = (value: string): number =>
    Math.min(19, Math.max(0, Number.parseInt(value, 10) || 0));

  const addItem = () => {
    if (!productId) return;
    void action.run(async () => {
      const payload = [
        ...placed.map(({ item, subpanel }) => ({
          product_id: item.product.id,
          subpanel_id: subpanel?.id ?? null,
          label: item.label,
          color: item.color,
          grid_row: item.grid_row,
          grid_col: item.grid_col,
          sort_order: item.sort_order,
        })),
        {
          product_id: productId,
          subpanel_id: subpanelId || null,
          label: label.trim() || null,
          color: color.trim() || null,
          grid_row: clampCell(gridRow),
          grid_col: clampCell(gridCol),
          sort_order: placed.length,
        },
      ];
      await apiFetch(`/api/v1/catalog/panels/${panel.id}/items`, {
        method: 'PUT',
        body: JSON.stringify({ items: payload }),
      });
      setProductId('');
      setLabel('');
      setColor('');
      onChanged();
    });
  };

  const productOf = (item: PanelItem): string => item.product.short_name ?? item.product.name;

  return (
    <>
      <div className="border-b border-slate-100 p-3">
        <h2 className="mb-2 text-sm font-bold uppercase tracking-wider text-slate-400">
          Items del panel
        </h2>
        {full ? (
          <Banner>Límite de {MAX_ITEMS} items alcanzado en este panel.</Banner>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            <Field label="Producto">
              <select className="input-base" value={productId} onChange={(e) => setProductId(e.target.value)}>
                <option value="" disabled>
                  Elige…
                </option>
                {available.map((product) => (
                  <option key={product.id} value={product.id}>
                    {product.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Subpanel">
              <select className="input-base" value={subpanelId} onChange={(e) => setSubpanelId(e.target.value)}>
                <option value="">— En el panel —</option>
                {panel.subpanels.map((subpanel) => (
                  <option key={subpanel.id} value={subpanel.id}>
                    {subpanel.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Etiqueta (opcional)">
              <input className="input-base" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={40} />
            </Field>
            <Field label="Color">
              <input type="color" className="input-base h-9" value={color || '#4f46e5'} onChange={(e) => setColor(e.target.value)} />
            </Field>
            <Field label="Fila (0-19)">
              <input type="number" min={0} max={19} className="input-base" value={gridRow} onChange={(e) => setGridRow(e.target.value)} />
            </Field>
            <div className="grid grid-cols-2 gap-2 sm:items-end">
              <Field label="Columna (0-19)">
                <input type="number" min={0} max={19} className="input-base" value={gridCol} onChange={(e) => setGridCol(e.target.value)} />
              </Field>
              <button
                type="button"
                className="btn-primary"
                onClick={addItem}
                disabled={action.busy || !productId}
              >
                Añadir
              </button>
            </div>
          </div>
        )}
      </div>

      {placed.length === 0 ? (
        <Empty>Este panel está vacío.</Empty>
      ) : (
        <table className="table-base">
          <thead>
            <tr>
              <th>Producto</th>
              <th>Etiqueta</th>
              <th>Color</th>
              <th>Pos</th>
              <th>Subpanel</th>
              <th aria-label="Acciones" />
            </tr>
          </thead>
          <tbody>
            {placed.map(({ item, subpanel }) => (
              <tr key={item.id}>
                <td className="font-medium">{productOf(item)}</td>
                <td>{item.label ?? '—'}</td>
                <td>
                  {item.color ? (
                    <span
                      aria-label={item.color}
                      className="inline-block h-4 w-4 rounded ring-1 ring-slate-300"
                      style={{ backgroundColor: item.color }}
                    />
                  ) : (
                    '—'
                  )}
                </td>
                <td className="font-mono text-xs">
                  {item.grid_row},{item.grid_col}
                </td>
                <td>{subpanel?.name ?? '—'}</td>
                <td className="text-right">
                  <button
                    type="button"
                    className="btn-danger !px-2 !py-0.5 text-xs"
                    onClick={() => removeItem(item)}
                    disabled={action.busy}
                  >
                    Quitar
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function EditPanelModal({ panel, onClose, onSaved }: {
  panel: Panel;
  onClose: () => void;
  onSaved: () => void;
}) {
  const action = useAction();
  const [name, setName] = useState(panel.name);
  const [sortOrder, setSortOrder] = useState(String(panel.sort_order));
  const [active, setActive] = useState(true); // GET /panels solo trae activos

  const save = () => {
    void action.run(async () => {
      await apiFetch(`/api/v1/catalog/panels/${panel.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name, sort_order: Number(sortOrder), active }),
      });
      onSaved();
    });
  };

  return (
    <Modal title={`Editar panel «${panel.name}»`} onClose={onClose}>
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}
      <Field label="Nombre">
        <input className="input-base" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
      </Field>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <Field label="Orden">
          <input type="number" className="input-base" value={sortOrder} onChange={(e) => setSortOrder(e.target.value)} />
        </Field>
        <label className="flex items-end gap-2 pb-2 text-sm">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          Activo
        </label>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className="btn-secondary" onClick={onClose}>
          Cancelar
        </button>
        <button type="button" className="btn-primary" onClick={save} disabled={action.busy}>
          Guardar
        </button>
      </div>
    </Modal>
  );
}

function EditSubpanelModal({ subpanel, onClose, onSaved }: {
  subpanel: SubPanel;
  onClose: () => void;
  onSaved: () => void;
}) {
  const action = useAction();
  const [name, setName] = useState(subpanel.name);
  const [sortOrder, setSortOrder] = useState(String(subpanel.sort_order));

  const save = () => {
    void action.run(async () => {
      await apiFetch(`/api/v1/catalog/subpanels/${subpanel.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name, sort_order: Number(sortOrder) }),
      });
      onSaved();
    });
  };

  return (
    <Modal title={`Renombrar «${subpanel.name}»`} onClose={onClose}>
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}
      <Field label="Nombre">
        <input className="input-base" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
      </Field>
      <div className="mt-3">
        <Field label="Orden">
          <input type="number" className="input-base" value={sortOrder} onChange={(e) => setSortOrder(e.target.value)} />
        </Field>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className="btn-secondary" onClick={onClose}>
          Cancelar
        </button>
        <button type="button" className="btn-primary" onClick={save} disabled={action.busy}>
          Guardar
        </button>
      </div>
    </Modal>
  );
}
