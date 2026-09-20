/**
 * Auditoría (/admin/audit): registro inmutable de quién cambió qué.
 * Filtros por acción (prefijo), usuario, entidad y rango temporal; cada
 * fila se expande para mostrar el antes/después en JSON.
 */

import { useState } from 'react';
import { apiFetch } from '../lib/api';
import { auditPageSchema, userPageSchema } from '../lib/domain';
import {
  Banner,
  Empty,
  Field,
  fmtDate,
  Loading,
  PageHeader,
  useAsync,
} from '../components/ui';

/** datetime-local → ISO con segundos (pydantic lo exige completo). */
const isoOf = (value: string) => (value.length === 16 ? `${value}:00` : value);

const jsonOf = (value: Record<string, unknown> | null): string =>
  value && Object.keys(value).length > 0 ? JSON.stringify(value, null, 2) : '—';

export default function AuditoriaPage() {
  const [action, setAction] = useState('admin.');
  const [userId, setUserId] = useState('');
  const [entity, setEntity] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [expanded, setExpanded] = useState<number | null>(null);

  const { data: users } = useAsync(
    () =>
      apiFetch<unknown>('/api/v1/admin/users?limit=200').then(userPageSchema.parse),
    [],
  );

  const { data, error, loading } = useAsync(() => {
    const params = new URLSearchParams({ limit: '50' });
    if (action.trim()) params.set('action', action.trim());
    if (userId) params.set('user_id', userId);
    if (entity.trim()) params.set('entity', entity.trim());
    if (from) params.set('occurred_from', isoOf(from));
    if (to) params.set('occurred_to', isoOf(to));
    return apiFetch<unknown>(`/api/v1/admin/audit?${params.toString()}`).then(auditPageSchema.parse);
  }, [action, userId, entity, from, to]);

  return (
    <>
      <PageHeader
        title="Auditoría"
        hint="Quién cambió qué y cuándo. Registro de solo lectura."
      />

      <div className="card mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5 sm:items-end">
        <Field label="Acción (prefijo)">
          <input className="input-base" value={action} onChange={(e) => setAction(e.target.value)} maxLength={64} placeholder="admin." />
        </Field>
        <Field label="Usuario">
          <select className="input-base" value={userId} onChange={(e) => setUserId(e.target.value)}>
            <option value="">Todos</option>
            {(users?.items ?? []).map((user) => (
              <option key={user.id} value={user.id}>
                {user.username}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Entidad">
          <input className="input-base" value={entity} onChange={(e) => setEntity(e.target.value)} maxLength={64} placeholder="user" />
        </Field>
        <Field label="Desde">
          <input type="datetime-local" className="input-base" value={from} onChange={(e) => setFrom(e.target.value)} />
        </Field>
        <Field label="Hasta">
          <input type="datetime-local" className="input-base" value={to} onChange={(e) => setTo(e.target.value)} />
        </Field>
      </div>

      <div className="card overflow-x-auto p-0">
        {loading ? (
          <Loading />
        ) : error ? (
          <Banner>{error}</Banner>
        ) : !data || data.items.length === 0 ? (
          <Empty>Sin registros para estos filtros.</Empty>
        ) : (
          <table className="table-base">
            <thead>
              <tr>
                <th>Fecha</th>
                <th>Usuario</th>
                <th>Acción</th>
                <th>Entidad</th>
                <th>IP</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((entry) => {
                const open = expanded === entry.id;
                return (
                  <tr
                    key={entry.id}
                    className="cursor-pointer hover:bg-indigo-50/60"
                    onClick={() => setExpanded(open ? null : entry.id)}
                    title="Ver detalle"
                  >
                    <td className="whitespace-nowrap">{fmtDate(entry.occurred_at)}</td>
                    <td>{entry.username ?? '—'}</td>
                    <td className="font-mono text-xs">{entry.action}</td>
                    <td className="font-mono text-xs">{entry.entity}</td>
                    <td className="font-mono text-xs">{entry.ip ?? '—'}</td>
                    {open && (
                      <td colSpan={5} className="bg-slate-50 p-0" onClick={(e) => e.stopPropagation()}>
                        <div className="grid gap-3 p-3 md:grid-cols-2">
                          <div>
                            <p className="mb-1 text-[11px] font-bold uppercase tracking-wider text-slate-400">Antes</p>
                            <pre className="max-h-56 overflow-auto rounded-lg bg-white p-2 text-xs text-slate-700 ring-1 ring-slate-200">
                              {jsonOf(entry.before_data)}
                            </pre>
                          </div>
                          <div>
                            <p className="mb-1 text-[11px] font-bold uppercase tracking-wider text-slate-400">Después</p>
                            <pre className="max-h-56 overflow-auto rounded-lg bg-white p-2 text-xs text-slate-700 ring-1 ring-slate-200">
                              {jsonOf(entry.after_data)}
                            </pre>
                          </div>
                        </div>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      {data && (
        <p className="mt-3 text-xs text-slate-400">
          {data.total} registro(s) · mostrando {data.items.length} (últimos primero).
        </p>
      )}
    </>
  );
}
