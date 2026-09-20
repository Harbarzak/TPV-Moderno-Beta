/**
 * Ajustes de sala: alta de ZONAS y MESAS (fase 30). Es el mínimo gesto de
 * configuración dentro del propio plano; renombrado/borrado fino sigue en
 * Administración (el backend ya expone PATCH para quien lo necesite).
 */

import { useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { Loader2, Plus } from 'lucide-react';
import { ApiError, apiFetch } from '../../lib/api';
import { useFloor } from '../../state/floor';
import { toast } from '../../state/toasts';

interface FloorSettingsDialogProps {
  onClose: () => void;
}

const inputClass =
  'h-11 w-full rounded-xl bg-surface px-3 text-ink outline-none ring-accent-hover focus:ring-2';

export default function FloorSettingsDialog({ onClose }: FloorSettingsDialogProps) {
  const zones = useFloor((state) => state.zones);
  const tables = useFloor((state) => state.tables);
  const reloadAll = useFloor((state) => state.reloadAll);

  const [selectedZoneId, setSelectedZoneId] = useState<string | null>(zones[0]?.id ?? null);
  const [zoneName, setZoneName] = useState('');
  const [tableName, setTableName] = useState('');
  const [seats, setSeats] = useState('4');
  const [posX, setPosX] = useState('');
  const [posY, setPosY] = useState('');
  const [busy, setBusy] = useState(false);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
      await reloadAll();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
      toast.error('No se pudo guardar', { key: 'floor-settings', detail });
    } finally {
      setBusy(false);
    }
  };

  const addZone = () =>
    run(async () => {
      const zone = await apiFetch<{ id: string }>('/api/v1/restaurant/zones', {
        method: 'POST',
        body: JSON.stringify({ name: zoneName.trim() }),
      });
      setZoneName('');
      setSelectedZoneId(zone.id);
      toast.success('Zona creada', { key: 'floor-settings' });
    });

  const addTable = () =>
    run(async () => {
      if (selectedZoneId === null) return;
      const parsedSeats = Number.parseInt(seats, 10);
      const body: Record<string, unknown> = {
        zone_id: selectedZoneId,
        name: tableName.trim(),
        seats: Number.isInteger(parsedSeats) && parsedSeats > 0 ? parsedSeats : 4,
      };
      // Posición opcional en el mapa (0-100 %, string — nunca float).
      const x = Number.parseFloat(posX.replace(',', '.'));
      const y = Number.parseFloat(posY.replace(',', '.'));
      if (Number.isFinite(x)) body.pos_x = x.toFixed(2);
      if (Number.isFinite(y)) body.pos_y = y.toFixed(2);
      await apiFetch('/api/v1/restaurant/tables', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      setTableName('');
      setPosX('');
      setPosY('');
      toast.success('Mesa creada', { key: 'floor-settings' });
    });

  const zoneTables = tables.filter((table) => table.zone_id === selectedZoneId);

  return (
    <Dialog.Root open onOpenChange={(next) => next || onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/60" />
        <Dialog.Content className="fixed left-1/2 top-1/2 max-h-[90vh] w-[min(40rem,94vw)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl bg-surface-raised p-5 shadow-2xl">
          <Dialog.Title className="text-lg font-bold text-ink">Ajustes de sala</Dialog.Title>
          <Dialog.Description className="mt-1 text-sm text-ink-muted">
            Crea zonas (Sala, Terraza, Barra…) y sus mesas.
          </Dialog.Description>

          <div className="mt-4 grid gap-5 md:grid-cols-2">
            {/* Zonas */}
            <section className="min-w-0">
              <h3 className="text-sm font-bold uppercase tracking-wide text-ink-muted">Zonas</h3>
              <ul className="mt-2 flex flex-col gap-1.5">
                {zones.map((zone) => (
                  <li key={zone.id}>
                    <button
                      type="button"
                      aria-pressed={selectedZoneId === zone.id}
                      onClick={() => setSelectedZoneId(zone.id)}
                      className={`flex h-11 w-full items-center justify-between rounded-xl px-3 text-left font-semibold transition active:scale-[0.98] ${
                        selectedZoneId === zone.id
                          ? 'bg-accent text-accent-ink'
                          : 'bg-surface text-ink hover:bg-surface-hover'
                      }`}
                    >
                      {zone.name}
                      <span className="text-xs font-normal tabular-nums opacity-75">
                        {tables.filter((table) => table.zone_id === zone.id).length} mesas
                      </span>
                    </button>
                  </li>
                ))}
                {zones.length === 0 && (
                  <li className="py-2 text-sm text-ink-muted">Sin zonas todavía.</li>
                )}
              </ul>
              <div className="mt-3 flex gap-2">
                <input
                  type="text"
                  maxLength={120}
                  value={zoneName}
                  onChange={(event) => setZoneName(event.target.value)}
                  placeholder="Nueva zona…"
                  className={inputClass}
                />
                <button
                  type="button"
                  onClick={() => void addZone()}
                  disabled={busy || zoneName.trim() === ''}
                  aria-label="Añadir zona"
                  className="grid size-11 shrink-0 place-items-center rounded-xl bg-accent text-accent-ink transition hover:bg-accent-hover active:scale-[0.98] disabled:opacity-40"
                >
                  <Plus className="size-5" aria-hidden />
                </button>
              </div>
            </section>

            {/* Mesas de la zona */}
            <section className="min-w-0">
              <h3 className="text-sm font-bold uppercase tracking-wide text-ink-muted">
                Mesas {selectedZoneId ? `de ${zones.find((z) => z.id === selectedZoneId)?.name ?? ''}` : ''}
              </h3>
              <ul className="mt-2 flex max-h-40 flex-wrap gap-1.5 overflow-y-auto">
                {zoneTables.map((table) => (
                  <li
                    key={table.id}
                    className="rounded-lg bg-surface px-3 py-1.5 text-sm font-semibold text-ink"
                  >
                    {table.name}
                  </li>
                ))}
                {zoneTables.length === 0 && (
                  <li className="py-2 text-sm text-ink-muted">Sin mesas en esta zona.</li>
                )}
              </ul>
              <div className="mt-3 flex flex-col gap-2">
                <div className="flex gap-2">
                  <input
                    type="text"
                    maxLength={120}
                    value={tableName}
                    onChange={(event) => setTableName(event.target.value)}
                    placeholder="Nombre (M5, Barra 1…)"
                    className={inputClass}
                  />
                  <input
                    type="number"
                    inputMode="numeric"
                    min={1}
                    max={99}
                    value={seats}
                    onChange={(event) => setSeats(event.target.value)}
                    aria-label="Plazas"
                    className={`${inputClass} w-20 shrink-0 tabular-nums`}
                  />
                </div>
                <div className="flex gap-2">
                  <input
                    type="number"
                    inputMode="decimal"
                    min={0}
                    max={100}
                    value={posX}
                    onChange={(event) => setPosX(event.target.value)}
                    placeholder="X % (opcional)"
                    aria-label="Posición X en el mapa, porcentaje"
                    className={`${inputClass} tabular-nums`}
                  />
                  <input
                    type="number"
                    inputMode="decimal"
                    min={0}
                    max={100}
                    value={posY}
                    onChange={(event) => setPosY(event.target.value)}
                    placeholder="Y % (opcional)"
                    aria-label="Posición Y en el mapa, porcentaje"
                    className={`${inputClass} tabular-nums`}
                  />
                  <button
                    type="button"
                    onClick={() => void addTable()}
                    disabled={busy || selectedZoneId === null || tableName.trim() === ''}
                    aria-label="Añadir mesa"
                    className="grid size-11 shrink-0 place-items-center rounded-xl bg-accent text-accent-ink transition hover:bg-accent-hover active:scale-[0.98] disabled:opacity-40"
                  >
                    {busy ? (
                      <Loader2 className="size-5 animate-spin" aria-hidden />
                    ) : (
                      <Plus className="size-5" aria-hidden />
                    )}
                  </button>
                </div>
              </div>
            </section>
          </div>

          <div className="mt-5 flex justify-end">
            <Dialog.Close asChild>
              <button
                type="button"
                className="rounded-lg bg-surface-sunken px-5 py-3 font-medium text-ink transition hover:bg-surface-hover"
              >
                Listo
              </button>
            </Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
