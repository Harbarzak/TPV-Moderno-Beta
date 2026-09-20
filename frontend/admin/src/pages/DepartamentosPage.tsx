/**
 * Departamentos del catálogo (/catalog/departments). La baja es SIEMPRE
 * lógica (DELETE del backend): el histórico de ventas conserva su FK.
 */

import { useState, type FormEvent } from 'react';
import { apiFetch } from '../lib/api';
import {
  departmentSchema,
  type Department,
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

const LIST = '/api/v1/catalog/departments?include_inactive=true';

export default function DepartamentosPage() {
  const { data, error, loading, reload } = useAsync(
    () => apiFetch<unknown>(LIST).then(departmentSchema.array().parse),
    [],
  );
  const create = useAction();
  const [editing, setEditing] = useState<Department | null>(null);

  const submitCreate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void create.run(async () => {
      await apiFetch('/api/v1/catalog/departments', {
        method: 'POST',
        body: JSON.stringify({
          code: String(form.get('code') ?? '').trim(),
          name: String(form.get('name') ?? '').trim(),
          sort_order: Number(form.get('sort_order') ?? 0),
        }),
      });
      reload();
    });
  };

  const deactivate = (department: Department) => {
    void create.run(async () => {
      await apiFetch(`/api/v1/catalog/departments/${department.id}`, { method: 'DELETE' });
      reload();
    });
  };

  return (
    <>
      <PageHeader
        title="Departamentos"
        hint="Primer nivel del catálogo: agrupan categorías y productos."
      />
      {create.error && <Banner onClose={create.clear}>{create.error}</Banner>}

      <form onSubmit={submitCreate} className="card mb-4 grid gap-3 sm:grid-cols-[1fr_2fr_90px_auto] sm:items-end">
        <Field label="Código">
          <input name="code" className="input-base" required maxLength={32} placeholder="BEBIDAS" />
        </Field>
        <Field label="Nombre">
          <input name="name" className="input-base" required maxLength={80} placeholder="Bebidas" />
        </Field>
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
        ) : !data || data.length === 0 ? (
          <Empty>Todavía no hay departamentos.</Empty>
        ) : (
          <table className="table-base">
            <thead>
              <tr>
                <th>Código</th>
                <th>Nombre</th>
                <th>Orden</th>
                <th>Estado</th>
                <th aria-label="Acciones" />
              </tr>
            </thead>
            <tbody>
              {data.map((department) => (
                <tr key={department.id}>
                  <td className="font-mono text-xs">{department.code}</td>
                  <td className="font-medium">{department.name}</td>
                  <td>{department.sort_order}</td>
                  <td>
                    <Badge ok={department.active} />
                  </td>
                  <td className="whitespace-nowrap text-right">
                    <button type="button" className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setEditing(department)}>
                      Editar
                    </button>{' '}
                    {department.active && (
                      <button
                        type="button"
                        className="btn-danger !px-2 !py-1 text-xs"
                        onClick={() => {
                          if (window.confirm(`¿Dar de baja «${department.name}»? El histórico se conserva.`)) {
                            deactivate(department);
                          }
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
          department={editing}
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

function EditModal({ department, onClose, onSaved }: {
  department: Department;
  onClose: () => void;
  onSaved: () => void;
}) {
  const action = useAction();
  const [name, setName] = useState(department.name);
  const [sortOrder, setSortOrder] = useState(String(department.sort_order));
  const [active, setActive] = useState(department.active);

  const save = () => {
    void action.run(async () => {
      await apiFetch(`/api/v1/catalog/departments/${department.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name, sort_order: Number(sortOrder), active }),
      });
      onSaved();
    });
  };

  return (
    <Modal title={`Editar «${department.code}»`} onClose={onClose}>
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
