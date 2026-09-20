/**
 * Dispositivos (/admin/devices): agentes tpv-agent, impresoras, pinpads,
 * cajones y displays. El token SOLO se muestra en el alta/rotación; en BD
 * queda su SHA-256. La baja es siempre lógica.
 */

import { useState, type FormEvent } from 'react';
import { apiFetch } from '../lib/api';
import {
  deviceEnrolledSchema,
  deviceListSchema,
  terminalListSchema,
  type Device,
  type DeviceEnrolled,
} from '../lib/domain';
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

const KINDS: ReadonlyArray<readonly [string, string]> = [
  ['agent', 'Agente (tpv-agent)'],
  ['printer', 'Impresora'],
  ['pinpad', 'Pinpad'],
  ['cashdrawer', 'Cajón portamonedas'],
  ['display', 'Display'],
];

const KIND_LABELS: Record<string, string> = Object.fromEntries(KINDS);

export default function DispositivosPage() {
  const [kindFilter, setKindFilter] = useState('');
  const { data, error, loading, reload } = useAsync(() => {
    const params = new URLSearchParams({ include_inactive: 'true' });
    if (kindFilter) params.set('kind', kindFilter);
    return apiFetch<unknown>(`/api/v1/admin/devices?${params.toString()}`).then(deviceListSchema.parse);
  }, [kindFilter]);
  const { data: terminals } = useAsync(
    () => apiFetch<unknown>('/api/v1/admin/terminals').then(terminalListSchema.parse),
    [],
  );

  const action = useAction();
  const [enrolled, setEnrolled] = useState<DeviceEnrolled | null>(null);
  const [editing, setEditing] = useState<Device | null>(null);

  const submitEnroll = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    const terminalId = String(form.get('terminal_id') ?? '');
    void action.run(async () => {
      const created = await apiFetch<unknown>('/api/v1/admin/devices', {
        method: 'POST',
        body: JSON.stringify({
          name: String(form.get('name') ?? '').trim(),
          kind: String(form.get('kind') ?? 'agent'),
          terminal_id: terminalId || null,
        }),
      }).then(deviceEnrolledSchema.parse);
      formEl.reset();
      setEnrolled(created);
      reload();
    });
  };

  const rotate = (device: Device) => {
    void action.run(async () => {
      const rotated = await apiFetch<unknown>(`/api/v1/admin/devices/${device.id}/rotate-token`, {
        method: 'POST',
      }).then(deviceEnrolledSchema.parse);
      setEnrolled(rotated);
      reload();
    });
  };

  const remove = (device: Device) => {
    void action.run(async () => {
      await apiFetch(`/api/v1/admin/devices/${device.id}`, { method: 'DELETE' });
      reload();
    });
  };

  const terminalName = (terminalId: string | null): string =>
    terminalId ? (terminals?.items.find((t) => t.id === terminalId)?.code ?? '—') : '—';

  return (
    <>
      <PageHeader
        title="Dispositivos"
        hint="Agentes e impresoras físicas enroladas; el token solo se muestra una vez."
      />
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}

      <form onSubmit={submitEnroll} className="card mb-4 grid gap-3 sm:grid-cols-[2fr_200px_200px_auto] sm:items-end">
        <Field label="Nombre">
          <input name="name" className="input-base" required maxLength={80} placeholder="Agente barra" />
        </Field>
        <Field label="Tipo">
          <select name="kind" className="input-base" defaultValue="agent">
            {KINDS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Terminal">
          <select name="terminal_id" className="input-base" defaultValue="">
            <option value="">— Sin terminal —</option>
            {(terminals?.items ?? []).filter((t) => t.active).map((terminal) => (
              <option key={terminal.id} value={terminal.id}>
                {terminal.code}
              </option>
            ))}
          </select>
        </Field>
        <button type="submit" className="btn-primary" disabled={action.busy}>
          Enrolar
        </button>
      </form>

      <div className="mb-3">
        <select
          className="input-base max-w-48"
          value={kindFilter}
          onChange={(e) => setKindFilter(e.target.value)}
          aria-label="Filtrar por tipo"
        >
          <option value="">Todos los tipos</option>
          {KINDS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      <div className="card overflow-x-auto p-0">
        {loading ? (
          <Loading />
        ) : error ? (
          <Banner>{error}</Banner>
        ) : !data || data.items.length === 0 ? (
          <Empty>No hay dispositivos enrolados.</Empty>
        ) : (
          <table className="table-base">
            <thead>
              <tr>
                <th>Nombre</th>
                <th>Tipo</th>
                <th>Terminal</th>
                <th>Última conexión</th>
                <th>Estado</th>
                <th aria-label="Acciones" />
              </tr>
            </thead>
            <tbody>
              {data.items.map((device) => (
                <tr key={device.id}>
                  <td className="font-medium">{device.name}</td>
                  <td>{KIND_LABELS[device.kind] ?? device.kind}</td>
                  <td>{terminalName(device.terminal_id)}</td>
                  <td>{fmtDate(device.last_seen_at)}</td>
                  <td>
                    <Badge ok={device.active} />
                  </td>
                  <td className="whitespace-nowrap text-right">
                    <button type="button" className="btn-secondary !px-2 !py-1 text-xs" onClick={() => rotate(device)}>
                      Rotar token
                    </button>{' '}
                    <button type="button" className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setEditing(device)}>
                      Editar
                    </button>{' '}
                    {device.active && (
                      <button
                        type="button"
                        className="btn-danger !px-2 !py-1 text-xs"
                        onClick={() => {
                          if (window.confirm(`¿Dar de baja «${device.name}»?`)) remove(device);
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

      {enrolled && <TokenModal enrolled={enrolled} onClose={() => setEnrolled(null)} />}
      {editing && (
        <EditModal
          device={editing}
          terminals={terminals?.items ?? []}
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

/** El token viaja en claro SOLO aquí: copiar ahora o perderlo. */
function TokenModal({ enrolled, onClose }: { enrolled: DeviceEnrolled; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard.writeText(enrolled.token).then(
      () => setCopied(true),
      () => setCopied(false),
    );
  };
  return (
    <Modal title={`Token de «${enrolled.device.name}»`} onClose={onClose}>
      <Banner>
        Copia el token AHORA: se muestra solo esta vez. El backend guarda únicamente su SHA-256;
        para recuperarlo habrá que rotarlo.
      </Banner>
      <pre className="max-h-40 overflow-auto break-all rounded-lg bg-slate-50 p-3 text-xs text-slate-700 ring-1 ring-slate-200">
        {enrolled.token}
      </pre>
      <div className="mt-4 flex justify-between gap-2">
        <button type="button" className="btn-secondary" onClick={copy}>
          {copied ? '¡Copiado!' : 'Copiar token'}
        </button>
        <button type="button" className="btn-primary" onClick={onClose}>
          Hecho
        </button>
      </div>
    </Modal>
  );
}

function EditModal({ device, terminals, onClose, onSaved }: {
  device: Device;
  terminals: Array<{ id: string; code: string; active: boolean }>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const action = useAction();
  const [name, setName] = useState(device.name);
  const [terminalId, setTerminalId] = useState(device.terminal_id ?? '');
  const [active, setActive] = useState(device.active);

  const save = () => {
    void action.run(async () => {
      await apiFetch(`/api/v1/admin/devices/${device.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name, terminal_id: terminalId || null, active }),
      });
      onSaved();
    });
  };

  return (
    <Modal title={`Editar «${device.name}»`} onClose={onClose}>
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}
      <Field label="Nombre">
        <input className="input-base" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
      </Field>
      <div className="mt-3">
        <Field label="Terminal">
          <select className="input-base" value={terminalId} onChange={(e) => setTerminalId(e.target.value)}>
            <option value="">— Sin terminal —</option>
            {terminals.filter((t) => t.active).map((terminal) => (
              <option key={terminal.id} value={terminal.id}>
                {terminal.code}
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
