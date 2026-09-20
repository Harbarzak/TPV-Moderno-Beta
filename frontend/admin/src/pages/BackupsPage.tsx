/**
 * Backups (/admin/backups): listado del directorio de volcados y
 * disparo manual de pg_dump. El backend responde 503 si no hay binario.
 */

import { useState } from 'react';
import { apiFetch } from '../lib/api';
import { backupFileSchema, backupListSchema } from '../lib/domain';
import {
  Banner,
  Empty,
  fmtBytes,
  fmtDate,
  Loading,
  PageHeader,
  useAction,
  useAsync,
} from '../components/ui';

export default function BackupsPage() {
  const { data, error, loading, reload } = useAsync(
    () => apiFetch<unknown>('/api/v1/admin/backups').then(backupListSchema.parse),
    [],
  );
  const run = useAction();
  const [okMsg, setOkMsg] = useState<string | null>(null);

  const runBackup = () => {
    void run.run(async () => {
      const created = await apiFetch<unknown>('/api/v1/admin/backups/run', { method: 'POST' })
        .then(backupFileSchema.parse);
      setOkMsg(`Backup creado: ${created.name} (${fmtBytes(created.size_bytes)})`);
      reload();
    });
  };

  return (
    <>
      <PageHeader
        title="Backups"
        hint="Volcados completos de la base de datos (pg_dump custom)."
        actions={
          <button type="button" className="btn-primary" onClick={runBackup} disabled={run.busy}>
            {run.busy ? 'Creando…' : 'Crear backup ahora'}
          </button>
        }
      />
      {run.error && <Banner onClose={run.clear}>{run.error}</Banner>}
      {okMsg && !run.error && (
        <Banner kind="ok" onClose={() => setOkMsg(null)}>
          {okMsg}
        </Banner>
      )}

      <div className="card overflow-x-auto p-0">
        {loading ? (
          <Loading />
        ) : error ? (
          <Banner>{error}</Banner>
        ) : !data || data.items.length === 0 ? (
          <Empty>No hay backups todavía. Crea el primero con el botón de arriba.</Empty>
        ) : (
          <table className="table-base">
            <thead>
              <tr>
                <th>Fichero</th>
                <th>Tamaño</th>
                <th>Fecha</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((file) => (
                <tr key={file.name}>
                  <td className="font-mono text-xs">{file.name}</td>
                  <td>{fmtBytes(file.size_bytes)}</td>
                  <td>{fmtDate(file.modified_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <p className="mt-3 text-xs text-slate-400">
        La contraseña de conexión nunca viaja en la línea de comandos: el backend la inyecta por
        entorno (PGPASSWORD). Los ficheros .dump NO contienen secretos del despliegue.
      </p>
    </>
  );
}
