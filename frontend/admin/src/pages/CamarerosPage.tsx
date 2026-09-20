/**
 * Camareros (/admin/users filtrado): el personal de SALA, separado de las
 * cuentas de administración. Opera el TPV con su PIN; solo se listan los
 * roles que no son de administración.
 */

import { useState, type FormEvent } from 'react';
import { apiFetch } from '../lib/api';
import { roleListSchema, userPageSchema, type User } from '../lib/domain';
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

export default function CamarerosPage() {
  const { data, error, loading, reload } = useAsync(
    () =>
      apiFetch<unknown>('/api/v1/admin/users?include_inactive=true&limit=200')
        .then(userPageSchema.parse),
    [],
  );
  const { data: roles } = useAsync(
    () => apiFetch<unknown>('/api/v1/admin/roles').then(roleListSchema.parse),
    [],
  );

  const staffRoles = (roles?.items ?? []).filter((role) => role.code !== 'admin');
  const staffCodes = new Set(staffRoles.map((role) => role.code));
  const staff = (data?.items ?? []).filter((user) => staffCodes.has(user.role_code));

  const create = useAction();
  const [editing, setEditing] = useState<User | null>(null);
  const [pinFor, setPinFor] = useState<User | null>(null);

  const submitCreate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    const pin = String(form.get('pin') ?? '').trim();
    void create.run(async () => {
      await apiFetch('/api/v1/admin/users', {
        method: 'POST',
        body: JSON.stringify({
          username: String(form.get('username') ?? '').trim(),
          full_name: String(form.get('full_name') ?? '').trim(),
          role_code: String(form.get('role_code') ?? ''),
          password: String(form.get('password') ?? ''),
          pin: /^\d{4,6}$/.test(pin) ? pin : null,
        }),
      });
      formEl.reset();
      reload();
    });
  };

  const deactivate = (user: User) => {
    void create.run(async () => {
      await apiFetch(`/api/v1/admin/users/${user.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ active: false }),
      });
      reload();
    });
  };

  return (
    <>
      <PageHeader
        title="Camareros"
        hint="Personal de sala: entran al TPV con su PIN. Las cuentas de administración están en «Usuarios»."
      />
      {create.error && <Banner onClose={create.clear}>{create.error}</Banner>}

      <form onSubmit={submitCreate} className="card mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5 sm:items-end">
        <Field label="Nombre">
          <input name="full_name" className="input-base" required maxLength={120} placeholder="Luis Pérez" />
        </Field>
        <Field label="Usuario">
          <input name="username" className="input-base" required minLength={2} maxLength={64} placeholder="luis.p" />
        </Field>
        <Field label="Rol">
          <select name="role_code" className="input-base" defaultValue={staffRoles[0]?.code ?? ''}>
            {staffRoles.map((role) => (
              <option key={role.id} value={role.code}>
                {role.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Contraseña (para este panel)">
          <input name="password" type="password" className="input-base" required minLength={8} maxLength={128} autoComplete="new-password" />
        </Field>
        <div className="grid gap-3 sm:grid-cols-[100px_auto] sm:items-end">
          <Field label="PIN del TPV">
            <input name="pin" className="input-base" inputMode="numeric" pattern="\d{4,6}" maxLength={6} placeholder="1234" />
          </Field>
          <button type="submit" className="btn-primary" disabled={create.busy}>
            Crear
          </button>
        </div>
      </form>

      <div className="card overflow-x-auto p-0">
        {loading ? (
          <Loading />
        ) : error ? (
          <Banner>{error}</Banner>
        ) : staff.length === 0 ? (
          <Empty>No hay personal de sala. Crea roles que no sean de administración en «Permisos».</Empty>
        ) : (
          <table className="table-base">
            <thead>
              <tr>
                <th>Nombre</th>
                <th>Usuario</th>
                <th>Rol</th>
                <th>PIN</th>
                <th>Último acceso</th>
                <th>Estado</th>
                <th aria-label="Acciones" />
              </tr>
            </thead>
            <tbody>
              {staff.map((user) => (
                <tr key={user.id}>
                  <td className="font-medium">{user.full_name}</td>
                  <td className="font-mono text-xs">{user.username}</td>
                  <td>{user.role_code}</td>
                  <td>
                    <Badge ok={user.has_pin} on="Con PIN" off="Sin PIN" />
                  </td>
                  <td>{fmtDate(user.last_login_at)}</td>
                  <td>
                    <Badge ok={user.active} />
                  </td>
                  <td className="whitespace-nowrap text-right">
                    <button type="button" className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setPinFor(user)}>
                      PIN
                    </button>{' '}
                    <button type="button" className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setEditing(user)}>
                      Editar
                    </button>{' '}
                    {user.active && (
                      <button
                        type="button"
                        className="btn-danger !px-2 !py-1 text-xs"
                        onClick={() => {
                          if (window.confirm(`¿Dar de baja a «${user.full_name}»?`)) deactivate(user);
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
          user={editing}
          staffRoles={staffRoles}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            reload();
          }}
        />
      )}
      {pinFor && (
        <PinModal
          user={pinFor}
          onClose={() => setPinFor(null)}
          onSaved={() => {
            setPinFor(null);
            reload();
          }}
        />
      )}
    </>
  );
}

function EditModal({ user, staffRoles, onClose, onSaved }: {
  user: User;
  staffRoles: Array<{ id: string; code: string; name: string }>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const action = useAction();
  const [fullName, setFullName] = useState(user.full_name);
  const [roleCode, setRoleCode] = useState(user.role_code);
  const [active, setActive] = useState(user.active);

  const save = () => {
    void action.run(async () => {
      await apiFetch(`/api/v1/admin/users/${user.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ full_name: fullName, role_code: roleCode, active }),
      });
      onSaved();
    });
  };

  return (
    <Modal title={`Editar «${user.username}»`} onClose={onClose}>
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}
      <Field label="Nombre">
        <input className="input-base" value={fullName} onChange={(e) => setFullName(e.target.value)} maxLength={120} />
      </Field>
      <div className="mt-3">
        <Field label="Rol">
          <select className="input-base" value={roleCode} onChange={(e) => setRoleCode(e.target.value)}>
            {staffRoles.map((role) => (
              <option key={role.id} value={role.code}>
                {role.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
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

function PinModal({ user, onClose, onSaved }: {
  user: User;
  onClose: () => void;
  onSaved: () => void;
}) {
  const action = useAction();
  const [pin, setPin] = useState('');
  const valid = /^\d{4,6}$/.test(pin);

  const save = () => {
    if (!valid) return;
    void action.run(async () => {
      await apiFetch(`/api/v1/admin/users/${user.id}/pin`, {
        method: 'PUT',
        body: JSON.stringify({ pin }),
      });
      onSaved();
    });
  };

  const clearPin = () => {
    void action.run(async () => {
      await apiFetch(`/api/v1/admin/users/${user.id}/pin`, {
        method: 'PUT',
        body: JSON.stringify({ pin: null }),
      });
      onSaved();
    });
  };

  return (
    <Modal title={`PIN de «${user.full_name}»`} onClose={onClose}>
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}
      <Field label="PIN (4-6 dígitos)">
        <input
          className="input-base font-mono text-lg tracking-widest"
          value={pin}
          onChange={(e) => setPin(e.target.value.replace(/\D/g, ''))}
          inputMode="numeric"
          maxLength={6}
          placeholder="1234"
        />
      </Field>
      <div className="mt-4 flex justify-between gap-2">
        {user.has_pin ? (
          <button type="button" className="btn-danger" onClick={clearPin} disabled={action.busy}>
            Quitar PIN
          </button>
        ) : (
          <span />
        )}
        <div className="flex gap-2">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancelar
          </button>
          <button type="button" className="btn-primary" onClick={save} disabled={action.busy || !valid}>
            Guardar
          </button>
        </div>
      </div>
    </Modal>
  );
}
