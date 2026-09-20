/**
 * Formas de pago (/admin/payment-methods): catálogo con el que la venta
 * cobra. `kind` es un enum cerrado del backend; aquí solo se elige.
 */

import { useState, type FormEvent } from 'react';
import { apiFetch } from '../lib/api';
import { paymentMethodListSchema, type PaymentMethod } from '../lib/domain';
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

const KINDS: ReadonlyArray<readonly [string, string]> = [
  ['cash', 'Efectivo'],
  ['card', 'Tarjeta'],
  ['voucher', 'Cheque/Valor'],
  ['credit', 'A crédito'],
  ['other', 'Otro'],
];

const KIND_LABELS: Record<string, string> = Object.fromEntries(KINDS);

export default function FormasPagoPage() {
  const { data, error, loading, reload } = useAsync(
    () =>
      apiFetch<unknown>('/api/v1/admin/payment-methods?include_inactive=true')
        .then(paymentMethodListSchema.parse),
    [],
  );
  const create = useAction();
  const [editing, setEditing] = useState<PaymentMethod | null>(null);

  const submitCreate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    void create.run(async () => {
      await apiFetch('/api/v1/admin/payment-methods', {
        method: 'POST',
        body: JSON.stringify({
          code: String(form.get('code') ?? '').trim(),
          name: String(form.get('name') ?? '').trim(),
          kind: String(form.get('kind') ?? 'cash'),
          opens_drawer: form.get('opens_drawer') === 'on',
          sort_order: Number(form.get('sort_order') ?? 0),
        }),
      });
      formEl.reset();
      reload();
    });
  };

  const remove = (method: PaymentMethod) => {
    void create.run(async () => {
      await apiFetch(`/api/v1/admin/payment-methods/${method.id}`, { method: 'DELETE' });
      reload();
    });
  };

  return (
    <>
      <PageHeader
        title="Formas de pago"
        hint="Medios de cobro disponibles en la operación diaria."
      />
      {create.error && <Banner onClose={create.clear}>{create.error}</Banner>}

      <form onSubmit={submitCreate} className="card mb-4 grid gap-3 sm:grid-cols-[110px_2fr_130px_110px_80px_auto] sm:items-end">
        <Field label="Código">
          <input name="code" className="input-base" required maxLength={40} placeholder="EFECTIVO" />
        </Field>
        <Field label="Nombre">
          <input name="name" className="input-base" required maxLength={80} placeholder="Efectivo" />
        </Field>
        <Field label="Tipo">
          <select name="kind" className="input-base" defaultValue="cash">
            {KINDS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <label className="flex items-end gap-2 pb-2.5 text-sm">
          <input type="checkbox" name="opens_drawer" />
          Abre cajón
        </label>
        <Field label="Orden">
          <input name="sort_order" type="number" className="input-base" defaultValue={0} />
        </Field>
        <button type="submit" className="btn-primary" disabled={create.busy}>
          Crear
        </button>
      </form>

      <div className="card overflow-x-auto p-0">
        {loading ? (
          <Loading />
        ) : error ? (
          <Banner>{error}</Banner>
        ) : !data || data.items.length === 0 ? (
          <Empty>Todavía no hay formas de pago.</Empty>
        ) : (
          <table className="table-base">
            <thead>
              <tr>
                <th>Código</th>
                <th>Nombre</th>
                <th>Tipo</th>
                <th>Cajón</th>
                <th>Orden</th>
                <th>Estado</th>
                <th aria-label="Acciones" />
              </tr>
            </thead>
            <tbody>
              {data.items.map((method) => (
                <tr key={method.id}>
                  <td className="font-mono text-xs">{method.code}</td>
                  <td className="font-medium">{method.name}</td>
                  <td>{KIND_LABELS[method.kind] ?? method.kind}</td>
                  <td>{method.opens_drawer ? 'Sí' : 'No'}</td>
                  <td>{method.sort_order}</td>
                  <td>
                    <Badge ok={method.active} />
                  </td>
                  <td className="whitespace-nowrap text-right">
                    <button type="button" className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setEditing(method)}>
                      Editar
                    </button>{' '}
                    {method.active && (
                      <button
                        type="button"
                        className="btn-danger !px-2 !py-1 text-xs"
                        onClick={() => {
                          if (window.confirm(`¿Dar de baja «${method.name}»?`)) remove(method);
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

      {editing && (
        <EditModal
          method={editing}
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

function EditModal({ method, onClose, onSaved }: {
  method: PaymentMethod;
  onClose: () => void;
  onSaved: () => void;
}) {
  const action = useAction();
  const [name, setName] = useState(method.name);
  const [kind, setKind] = useState(method.kind);
  const [opensDrawer, setOpensDrawer] = useState(method.opens_drawer);
  const [sortOrder, setSortOrder] = useState(String(method.sort_order));
  const [active, setActive] = useState(method.active);

  const save = () => {
    void action.run(async () => {
      await apiFetch(`/api/v1/admin/payment-methods/${method.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name,
          kind,
          opens_drawer: opensDrawer,
          sort_order: Number(sortOrder),
          active,
        }),
      });
      onSaved();
    });
  };

  return (
    <Modal title={`Editar «${method.code}»`} onClose={onClose}>
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}
      <Field label="Nombre">
        <input className="input-base" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
      </Field>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <Field label="Tipo">
          <select className="input-base" value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Orden">
          <input type="number" className="input-base" value={sortOrder} onChange={(e) => setSortOrder(e.target.value)} />
        </Field>
      </div>
      <div className="mt-3 flex gap-4 text-sm">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={opensDrawer} onChange={(e) => setOpensDrawer(e.target.checked)} />
          Abre cajón
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          Activa
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
