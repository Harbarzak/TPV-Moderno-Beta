/**
 * Categorías (/catalog/categories): segundo nivel, colgando de un
 * departamento opcional. Misma pauta que departamentos: alta, edición y
 * baja lógica.
 */

import { useState, type FormEvent } from 'react';
import { apiFetch } from '../lib/api';
import { categorySchema, departmentSchema, type Category, type Department } from '../lib/domain';
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

export default function CategoriasPage() {
  const { data: categories, error, loading, reload } = useAsync(
    () =>
      apiFetch<unknown>('/api/v1/catalog/categories?include_inactive=true')
        .then(categorySchema.array().parse),
    [],
  );
  const { data: departments } = useAsync(
    () =>
      apiFetch<unknown>('/api/v1/catalog/departments')
        .then(departmentSchema.array().parse),
    [],
  );
  const create = useAction();
  const [editing, setEditing] = useState<Category | null>(null);

  const byId = new Map((departments ?? []).map((department) => [department.id, department]));

  const submitCreate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const departmentId = String(form.get('department_id') ?? '');
    void create.run(async () => {
      await apiFetch('/api/v1/catalog/categories', {
        method: 'POST',
        body: JSON.stringify({
          name: String(form.get('name') ?? '').trim(),
          department_id: departmentId || null,
          sort_order: Number(form.get('sort_order') ?? 0),
        }),
      });
      (event.target as HTMLFormElement).reset();
      reload();
    });
  };

  return (
    <>
      <PageHeader
        title="Categorías"
        hint="Agrupan productos dentro de un departamento (opcional)."
      />
      {create.error && <Banner onClose={create.clear}>{create.error}</Banner>}

      <form onSubmit={submitCreate} className="card mb-4 grid gap-3 sm:grid-cols-[2fr_2fr_90px_auto] sm:items-end">
        <Field label="Nombre">
          <input name="name" className="input-base" required maxLength={80} placeholder="Cafés" />
        </Field>
        <Field label="Departamento">
          <select name="department_id" className="input-base" defaultValue="">
            <option value="">— Sin departamento —</option>
            {(departments ?? []).map((department) => (
              <option key={department.id} value={department.id}>
                {department.name}
              </option>
            ))}
          </select>
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
        ) : !categories || categories.length === 0 ? (
          <Empty>Todavía no hay categorías.</Empty>
        ) : (
          <table className="table-base">
            <thead>
              <tr>
                <th>Nombre</th>
                <th>Departamento</th>
                <th>Orden</th>
                <th>Estado</th>
                <th aria-label="Acciones" />
              </tr>
            </thead>
            <tbody>
              {categories.map((category) => (
                <tr key={category.id}>
                  <td className="font-medium">{category.name}</td>
                  <td>
                    {category.department_id
                      ? (byId.get(category.department_id)?.name ?? '—')
                      : '—'}
                  </td>
                  <td>{category.sort_order}</td>
                  <td>
                    <Badge ok={category.active} />
                  </td>
                  <td className="text-right">
                    <button type="button" className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setEditing(category)}>
                      Editar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {editing && (
        <EditModal
          category={editing}
          departments={departments ?? []}
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

function EditModal({ category, departments, onClose, onSaved }: {
  category: Category;
  departments: Department[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const action = useAction();
  const [name, setName] = useState(category.name);
  const [departmentId, setDepartmentId] = useState(category.department_id ?? '');
  const [sortOrder, setSortOrder] = useState(String(category.sort_order));
  const [active, setActive] = useState(category.active);

  const save = () => {
    void action.run(async () => {
      await apiFetch(`/api/v1/catalog/categories/${category.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name,
          department_id: departmentId || null,
          sort_order: Number(sortOrder),
          active,
        }),
      });
      onSaved();
    });
  };

  const remove = () => {
    if (!window.confirm(`¿Dar de baja «${category.name}»? El histórico se conserva.`)) return;
    void action.run(async () => {
      await apiFetch(`/api/v1/catalog/categories/${category.id}`, { method: 'DELETE' });
      onSaved();
    });
  };

  return (
    <Modal title={`Editar «${category.name}»`} onClose={onClose}>
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}
      <Field label="Nombre">
        <input className="input-base" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
      </Field>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <Field label="Departamento">
          <select className="input-base" value={departmentId} onChange={(e) => setDepartmentId(e.target.value)}>
            <option value="">— Sin departamento —</option>
            {departments.map((department) => (
              <option key={department.id} value={department.id}>
                {department.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Orden">
          <input type="number" className="input-base" value={sortOrder} onChange={(e) => setSortOrder(e.target.value)} />
        </Field>
      </div>
      <label className="mt-3 flex items-center gap-2 text-sm">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        Activa
      </label>
      <div className="mt-4 flex justify-between gap-2">
        <button type="button" className="btn-danger" onClick={remove} disabled={action.busy}>
          Dar de baja
        </button>
        <div className="flex gap-2">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancelar
          </button>
          <button type="button" className="btn-primary" onClick={save} disabled={action.busy}>
            Guardar
          </button>
        </div>
      </div>
    </Modal>
  );
}
