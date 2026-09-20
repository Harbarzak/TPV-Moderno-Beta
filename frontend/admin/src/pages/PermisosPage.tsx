/**
 * Permisos (/admin/roles + /admin/permissions): matriz rol × permiso
 * agrupada por espacio de nombres (products.*, sales.*, admin.*…).
 * El rol «admin» es de sistema: su matriz es inmutable desde la API.
 */

import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { apiFetch } from '../lib/api';
import {
  permissionListSchema,
  roleListSchema,
  roleSchema,
  type Permission,
} from '../lib/domain';
import {
  Banner,
  Field,
  Loading,
  PageHeader,
  useAction,
  useAsync,
} from '../components/ui';

export default function PermisosPage() {
  const { data: roles, error, loading, reload } = useAsync(
    () => apiFetch<unknown>('/api/v1/admin/roles').then(roleListSchema.parse),
    [],
  );
  const { data: catalog } = useAsync(
    () => apiFetch<unknown>('/api/v1/admin/permissions').then(permissionListSchema.parse),
    [],
  );

  const action = useAction();
  const [roleId, setRoleId] = useState('');
  const [pending, setPending] = useState<string[]>([]);
  const [nameDraft, setNameDraft] = useState('');

  const role = (roles?.items ?? []).find((item) => item.id === roleId) ?? roles?.items[0] ?? null;
  const locked = role?.is_system ?? false;

  // Al cambiar de rol se parte de su matriz tal y como está en el servidor.
  useEffect(() => {
    setPending(role ? [...role.permissions] : []);
    setNameDraft(role?.name ?? '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [role?.id]);

  const groups = useMemo(() => {
    const byNamespace = new Map<string, Permission[]>();
    for (const perm of catalog?.items ?? []) {
      const namespace = perm.code.split('.')[0] ?? 'otros';
      const list = byNamespace.get(namespace) ?? [];
      list.push(perm);
      byNamespace.set(namespace, list);
    }
    return [...byNamespace.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [catalog]);

  const dirty =
    role !== null &&
    [...pending].sort().join('|') !== [...role.permissions].sort().join('|');

  const toggle = (code: string) => {
    setPending((prev) =>
      prev.includes(code) ? prev.filter((item) => item !== code) : [...prev, code],
    );
  };

  const submitRole = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    void action.run(async () => {
      const created = await apiFetch<unknown>('/api/v1/admin/roles', {
        method: 'POST',
        body: JSON.stringify({
          code: String(form.get('code') ?? '').trim(),
          name: String(form.get('name') ?? '').trim(),
        }),
      }).then(roleSchema.parse);
      formEl.reset();
      reload();
      setRoleId(created.id);
    });
  };

  const saveMatrix = () => {
    if (!role) return;
    void action.run(async () => {
      await apiFetch(`/api/v1/admin/roles/${role.id}/permissions`, {
        method: 'PUT',
        body: JSON.stringify({ permissions: [...pending].sort() }),
      });
      reload();
    });
  };

  const rename = () => {
    if (!role || !nameDraft.trim()) return;
    void action.run(async () => {
      await apiFetch(`/api/v1/admin/roles/${role.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name: nameDraft.trim() }),
      });
      reload();
    });
  };

  return (
    <>
      <PageHeader
        title="Roles y permisos"
        hint="Qué puede hacer cada rol. Los cambios aplican en el próximo arranque de sesión."
      />
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}
      {locked && role && (
        <Banner kind="ok">
          El rol «{role.code}» es de sistema: su matriz de permisos está protegida y no se puede
          modificar.
        </Banner>
      )}

      <form onSubmit={submitRole} className="card mb-4 grid gap-3 sm:grid-cols-[2fr_2fr_auto] sm:items-end">
        <Field label="Código (minúsculas, guiones bajos)">
          <input name="code" className="input-base" required pattern="[a-z][a-z0-9_]{1,31}" maxLength={32} placeholder="supervisor" />
        </Field>
        <Field label="Nombre">
          <input name="name" className="input-base" required maxLength={80} placeholder="Supervisor" />
        </Field>
        <button type="submit" className="btn-primary" disabled={action.busy}>
          Crear rol
        </button>
      </form>

      <div className="card overflow-x-auto p-0">
        {loading ? (
          <Loading />
        ) : error ? (
          <Banner>{error}</Banner>
        ) : !roles || roles.items.length === 0 ? (
          <Banner>No hay roles.</Banner>
        ) : (
          <table className="table-base">
            <thead>
              <tr>
                <th>Código</th>
                <th>Nombre</th>
                <th>Permisos</th>
                <th>Tipo</th>
              </tr>
            </thead>
            <tbody>
              {roles.items.map((item) => (
                <tr
                  key={item.id}
                  className={`cursor-pointer ${role?.id === item.id ? 'bg-indigo-50/70' : 'hover:bg-indigo-50/40'}`}
                  onClick={() => setRoleId(item.id)}
                >
                  <td className="font-mono text-xs">{item.code}</td>
                  <td className="font-medium">{item.name}</td>
                  <td>{item.permissions.length}</td>
                  <td>{item.is_system ? 'Sistema' : 'Propio'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {role && (
        <div className="mt-4">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <h2 className="text-base font-bold text-slate-800">
              Permisos de «{role.code}»
            </h2>
            <input
              className="input-base max-w-56"
              value={nameDraft}
              onChange={(e) => setNameDraft(e.target.value)}
              maxLength={80}
              aria-label="Nombre del rol"
              disabled={action.busy}
            />
            <button
              type="button"
              className="btn-secondary !px-2 !py-1 text-xs"
              onClick={rename}
              disabled={action.busy || !nameDraft.trim() || nameDraft === role.name}
            >
              Renombrar
            </button>
            <span className="grow" />
            <button
              type="button"
              className="btn-primary"
              onClick={saveMatrix}
              disabled={locked || action.busy || !dirty}
            >
              {dirty ? 'Guardar cambios' : 'Sin cambios'}
            </button>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            {groups.map(([namespace, perms]) => (
              <div key={namespace} className="card p-3">
                <p className="mb-2 text-[11px] font-bold uppercase tracking-widest text-slate-400">
                  {namespace}
                </p>
                {perms.map((perm) => (
                  <label key={perm.code} className="flex items-start gap-2 py-1 text-sm">
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={pending.includes(perm.code)}
                      onChange={() => toggle(perm.code)}
                      disabled={locked || action.busy}
                    />
                    <span>
                      <span className="font-mono text-xs text-slate-700">{perm.code}</span>
                      <span className="block text-xs text-slate-400">{perm.description}</span>
                    </span>
                  </label>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
