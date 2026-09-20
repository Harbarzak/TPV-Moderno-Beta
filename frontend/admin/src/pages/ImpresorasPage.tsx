/**
 * Impresoras (/admin/printers): tiquets, cocina y facturas, por red o vía
 * agente. kind/conexión son inmutables tras el alta (PATCH limitado);
 * «Probar» encola un trabajo de impresión de prueba.
 */

import { useState, type FormEvent } from 'react';
import { apiFetch } from '../lib/api';
import {
  deviceListSchema,
  printerListSchema,
  type Printer,
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

const KINDS: ReadonlyArray<readonly [string, string]> = [
  ['receipt', 'Tiquets'],
  ['kitchen', 'Cocina'],
  ['invoice', 'Facturas'],
];

const CONNECTIONS: ReadonlyArray<readonly [string, string]> = [
  ['network', 'Red (IP:puerto)'],
  ['agent', 'Vía agente'],
];

const WIDTHS: ReadonlyArray<readonly [number, string]> = [
  [32, '32 columnas'],
  [42, '42 columnas'],
  [48, '48 columnas'],
];

const labelOf = (pairs: ReadonlyArray<readonly [string, string]>, value: string): string =>
  pairs.find(([key]) => key === value)?.[1] ?? value;

export default function ImpresorasPage() {
  const { data, error, loading, reload } = useAsync(
    () =>
      apiFetch<unknown>('/api/v1/admin/printers?include_inactive=true')
        .then(printerListSchema.parse),
    [],
  );
  const { data: devices } = useAsync(
    () =>
      apiFetch<unknown>('/api/v1/admin/devices?kind=agent')
        .then(deviceListSchema.parse)
        .catch(() => ({ items: [] })),
    [],
  );

  const create = useAction();
  const [editing, setEditing] = useState<Printer | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);

  const submitCreate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    const connection = String(form.get('connection') ?? 'network');
    void create.run(async () => {
      await apiFetch('/api/v1/admin/printers', {
        method: 'POST',
        body: JSON.stringify({
          name: String(form.get('name') ?? '').trim(),
          kind: String(form.get('kind') ?? 'receipt'),
          connection,
          address:
            connection === 'network' ? String(form.get('address') ?? '').trim() : null,
          device_id:
            connection === 'agent'
              ? String(form.get('device_id') ?? '') || null
              : null,
          width_chars: Number(form.get('width_chars') ?? 42),
          is_default: form.get('is_default') === 'on',
        }),
      });
      formEl.reset();
      reload();
    });
  };

  const testPrint = (printer: Printer) => {
    void create.run(async () => {
      const job = await apiFetch<Record<string, unknown>>(`/api/v1/admin/printers/${printer.id}/test`, {
        method: 'POST',
      });
      setOkMsg(`Trabajo de prueba enviado a «${printer.name}» (estado: ${String(job.status ?? 'encolado')}).`);
    });
  };

  const remove = (printer: Printer) => {
    void create.run(async () => {
      await apiFetch(`/api/v1/admin/printers/${printer.id}`, { method: 'DELETE' });
      reload();
    });
  };

  const deviceLabel = (deviceId: string | null): string =>
    deviceId ? (devices?.items.find((d) => d.id === deviceId)?.name ?? '—') : '—';

  return (
    <>
      <PageHeader
        title="Impresoras"
        hint="Destinos de impresión de tiquets, comandas de cocina y facturas."
      />
      {create.error && <Banner onClose={create.clear}>{create.error}</Banner>}
      {okMsg && !create.error && (
        <Banner kind="ok" onClose={() => setOkMsg(null)}>
          {okMsg}
        </Banner>
      )}

      <form onSubmit={submitCreate} className="card mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 sm:items-end">
        <Field label="Nombre">
          <input name="name" className="input-base" required maxLength={80} placeholder="Cocina barra" />
        </Field>
        <Field label="Tipo">
          <select name="kind" className="input-base" defaultValue="receipt">
            {KINDS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Conexión">
          <select name="connection" className="input-base" defaultValue="network">
            {CONNECTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Ancho">
          <select name="width_chars" className="input-base" defaultValue="42">
            {WIDTHS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Dirección de red">
          <input name="address" className="input-base" maxLength={120} placeholder="192.168.1.50:9100" />
        </Field>
        <Field label="Dispositivo agente">
          <select name="device_id" className="input-base" defaultValue="">
            <option value="">—</option>
            {(devices?.items ?? []).filter((d) => d.active).map((device) => (
              <option key={device.id} value={device.id}>
                {device.name}
              </option>
            ))}
          </select>
        </Field>
        <label className="flex items-end gap-2 pb-2.5 text-sm">
          <input type="checkbox" name="is_default" />
          Por defecto
        </label>
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
          <Empty>No hay impresoras configuradas.</Empty>
        ) : (
          <table className="table-base">
            <thead>
              <tr>
                <th>Nombre</th>
                <th>Tipo</th>
                <th>Conexión</th>
                <th>Destino</th>
                <th>Ancho</th>
                <th>Defecto</th>
                <th>Estado</th>
                <th aria-label="Acciones" />
              </tr>
            </thead>
            <tbody>
              {data.items.map((printer) => (
                <tr key={printer.id}>
                  <td className="font-medium">{printer.name}</td>
                  <td>{labelOf(KINDS, printer.kind)}</td>
                  <td>{labelOf(CONNECTIONS, printer.connection)}</td>
                  <td className="font-mono text-xs">
                    {printer.connection === 'network' ? (printer.address ?? '—') : deviceLabel(printer.device_id)}
                  </td>
                  <td>{printer.width_chars}</td>
                  <td>{printer.is_default ? 'Sí' : 'No'}</td>
                  <td>
                    <Badge ok={printer.active} />
                  </td>
                  <td className="whitespace-nowrap text-right">
                    <button type="button" className="btn-secondary !px-2 !py-1 text-xs" onClick={() => testPrint(printer)}>
                      Probar
                    </button>{' '}
                    <button type="button" className="btn-secondary !px-2 !py-1 text-xs" onClick={() => setEditing(printer)}>
                      Editar
                    </button>{' '}
                    {printer.active && (
                      <button
                        type="button"
                        className="btn-danger !px-2 !py-1 text-xs"
                        onClick={() => {
                          if (window.confirm(`¿Dar de baja «${printer.name}»?`)) remove(printer);
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
          printer={editing}
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

function EditModal({ printer, onClose, onSaved }: {
  printer: Printer;
  onClose: () => void;
  onSaved: () => void;
}) {
  const action = useAction();
  const [name, setName] = useState(printer.name);
  const [address, setAddress] = useState(printer.address ?? '');
  const [widthChars, setWidthChars] = useState(String(printer.width_chars));
  const [isDefault, setIsDefault] = useState(printer.is_default);
  const [active, setActive] = useState(printer.active);

  const save = () => {
    void action.run(async () => {
      await apiFetch(`/api/v1/admin/printers/${printer.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name,
          address: printer.connection === 'network' ? address.trim() : undefined,
          width_chars: Number(widthChars),
          is_default: isDefault,
          active,
        }),
      });
      onSaved();
    });
  };

  return (
    <Modal title={`Editar «${printer.name}»`} onClose={onClose}>
      {action.error && <Banner onClose={action.clear}>{action.error}</Banner>}
      <Field label="Nombre">
        <input className="input-base" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
      </Field>
      {printer.connection === 'network' && (
        <div className="mt-3">
          <Field label="Dirección de red">
            <input className="input-base" value={address} onChange={(e) => setAddress(e.target.value)} maxLength={120} />
          </Field>
        </div>
      )}
      <div className="mt-3 grid grid-cols-2 gap-3">
        <Field label="Ancho">
          <select className="input-base" value={widthChars} onChange={(e) => setWidthChars(e.target.value)}>
            {WIDTHS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <div className="flex flex-col justify-end gap-1 pb-1 text-sm">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />
            Por defecto
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
            Activa
          </label>
        </div>
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
