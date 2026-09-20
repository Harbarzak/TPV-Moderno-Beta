/**
 * Terminales (/admin/terminals): cada punto de venta físico. La baja es
 * SIEMPRE lógica (DELETE del backend) para conservar el histórico.
 */

import { useState, type FormEvent } from 'react';
import { apiFetch } from '../lib/api';
import { terminalListSchema, type Terminal } from '../lib/domain';
import {
  Badge,
  Banner,
  Empty,
  Field,
  fmtDate,
  Loading,
  Modal,
  PageHeader,
  useAction,
  useAsync,
} from '../components/ui';

export default function TerminalesPage() {
  const { data, error, loading, reload } = useAsync(
    () =>
      apiFetch<unknown>('/api/v1/admin/terminals?include_inactive=true')
        .then(terminalListSchema.parse),
    [],
  );
  const create = useAction();
  const [editing, setEditing] = useState<Terminal | null>(null);

  const submitCreate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    void create.run(async () => {
      await apiFetch('/api/v1/admin/terminals', {
        method: 'POST',
        body: JSON.stringify({
          code: String(form.get('code') ?? '').trim(),
          name: String(form.get('name') ?? '').trim(),
        }),
      });
      formEl.reset();
      reload();
    });
  };

  const remove = (terminal: Terminal) => {
    void create.run(async () => {
      await apiFetch(`/api/v1/admin/terminals/${terminal.id}`, { method: 'DELETE' });
      reload();
    });
  };

  return (
    <>
      <PageHeader
        title="Terminales"
        hint="Puntos de venta físicos (TPV-01, TPV-02…)."
      />
      {create.error && <Banner onClose={create.clear}>{create.error}</Banner>}

      <form onSubmit={submitCreate} className="card mb-4 grid gap-3 sm:grid-cols-[160px_2fr_auto] sm:items-end">
        <Field label="Código">
          <input name="code" className="input-base" required maxLength={32} placeholder="TPV-01" />
        </Field>
        <Field label="Nombre">
          <input name="name" className="input-base" required maxLength={80} placeholder="Barra principal" />
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
          <Empty>Todavía no hay terminales.</Empty>
        ) : (
          <table className="table-base">
            <thead>
              <tr>
                <th>Código</th>
                <th>Nombre</th>
                <th>Estado</th>
                <th>Creado</th>
                <th aria-label="Acciones" />
              </tr>
            </thead>
            <tbody>
              {data.items.map((terminal) => (
                <tr key={terminal.id}>
                  <td className="font-mono text-xs">{terminal.code}</td>
                  <td className="font-medium">{terminal.name}</td>
                  <td>
                    <Badge ok={terminal.active} />
                  </td>
                  <td>{fmtDate(terminal.created_at)}</td>
                  <td className="whitespace-nowrap text-right">
                    <button type="button" className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setEditing(terminal)}>
                      Editar
                    </button>{' '}
                    {terminal.active && (
                      <button
                        type="button"
                        className="btn-danger !px-2 !py-1 text-xs"
                        onClick={() => {
                          if (window.confirm(`¿Dar de baja «${terminal.code}»?`)) remove(terminal);
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
          terminal={editing}
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

function EditModal({ terminal, onClose, onSaved }: {
  terminal: Terminal;
  onClose: () => void;
  onSaved: () => void;
}) {
  const action = useAction();
  const [name, setName] = useState(terminal.name);
  const [active, setActive] = useState(terminal.active);

  const save = () => {
    void action.run(async () => {
      await apiFetch(`/api/v1/admin/terminals/${terminal.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name, active }),
      });
      onSaved();
    });
  };

  return (
    <Modal title={`Editar «${terminal.code}»`} onClose={onClose}>
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}
      <Field label="Nombre">
        <input className="input-base" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
      </Field>
      <label className="mt-3 flex items-center gap-2 text-sm">
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        Activo
      </label>
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
