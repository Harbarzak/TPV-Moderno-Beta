/**
 * Barra superior (design-system.md §5.3): el estado del puesto SIEMPRE visible
 * — conexión, terminal y caja, con TEXTO y no solo color (§2 accesibilidad) —
 * más recarga de catálogo, búsqueda, ayuda, informes, usuario y salida.
 */

import {
  Armchair,
  BarChart3,
  CircleHelp,
  Lock,
  LockOpen,
  LogOut,
  Monitor,
  RefreshCw,
  Search,
  Wifi,
  WifiOff,
} from 'lucide-react';

interface TopBarProps {
  user: string;
  online: boolean;
  /** null = sin configurar: el chip invita a configurarlo. */
  terminalLabel: string | null;
  cashOpen: boolean;
  /** Texto extra del chip de caja («· 20,00 €» del fondo inicial). */
  cashLabel: string | null;
  onTerminal: () => void;
  onCash: () => void;
  onSearch: () => void;
  onHelp: () => void;
  onReports: () => void;
  /** Presente solo si el puesto tiene modo sala (restaurante); oculta si no. */
  onFloor?: () => void;
  onReload: () => void;
  onLogout: () => void;
}

const BUTTON =
  'flex items-center gap-2 rounded-lg bg-slate-700 px-4 py-2.5 text-base font-medium ' +
  'text-slate-100 transition hover:bg-slate-600 active:bg-slate-500';

const CHIP = 'flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-semibold transition active:scale-[0.98]';

export default function TopBar({
  user,
  online,
  terminalLabel,
  cashOpen,
  cashLabel,
  onTerminal,
  onCash,
  onSearch,
  onHelp,
  onReports,
  onFloor,
  onReload,
  onLogout,
}: TopBarProps) {
  return (
    <header className="flex items-center gap-2 border-b border-slate-700 bg-slate-800 px-4 py-3">
      <h1 className="mr-1 text-xl font-bold tracking-wide text-amber-400">TPV</h1>

      <span
        role="status"
        className={`${CHIP} ${online ? 'bg-emerald-500/15 text-emerald-400' : 'bg-amber-500/15 text-amber-300'}`}
        title={online ? 'Conexión con el servidor correcta' : 'Sin conexión: la venta sigue con la copia guardada'}
      >
        {online ? <Wifi className="size-4" aria-hidden /> : <WifiOff className="size-4" aria-hidden />}
        <span className="hidden md:inline">{online ? 'En línea' : 'Sin conexión'}</span>
      </span>

      <button
        type="button"
        onClick={onTerminal}
        title={terminalLabel ? `Terminal: ${terminalLabel} (pulsa para cambiar)` : 'Configura el terminal de este puesto'}
        className={`${CHIP} ${terminalLabel ? 'bg-slate-700 text-slate-200 hover:bg-slate-600' : 'bg-amber-500/20 text-amber-300 hover:bg-amber-500/30'}`}
      >
        <Monitor className="size-4" aria-hidden />
        <span className="hidden md:inline">{terminalLabel ?? 'Configurar terminal'}</span>
      </button>

      <button
        type="button"
        onClick={onCash}
        title={cashOpen ? 'Estado de la caja (abierta)' : 'Abrir la caja del terminal'}
        className={`${CHIP} ${cashOpen ? 'bg-emerald-500/15 text-emerald-400' : 'bg-amber-500/20 text-amber-300'}`}
      >
        {cashOpen ? <Lock className="size-4" aria-hidden /> : <LockOpen className="size-4" aria-hidden />}
        <span className="hidden md:inline">
          {cashOpen ? `Caja abierta${cashLabel ? ` ${cashLabel}` : ''}` : 'Caja cerrada'}
        </span>
      </button>

      <div className="min-w-0 flex-1" aria-hidden />

      <button type="button" className={BUTTON} onClick={onReload} title="Recargar catálogo">
        <RefreshCw className="size-5" aria-hidden />
        <span className="hidden xl:inline">Recargar</span>
      </button>
      <button type="button" className={BUTTON} onClick={onSearch} title="Buscar producto (F2)">
        <Search className="size-5" aria-hidden />
        <span className="hidden xl:inline">Buscar (F2)</span>
      </button>
      <button type="button" className={BUTTON} onClick={onHelp} title="Ayuda (F1)">
        <CircleHelp className="size-5" aria-hidden />
        <span className="hidden xl:inline">Ayuda (F1)</span>
      </button>
      {/* El acceso es visible siempre; sin reports.view el backend responde
          403 y la vista de informes lo muestra (es la última palabra). */}
      <button type="button" className={BUTTON} onClick={onReports} title="Informes">
        <BarChart3 className="size-5" aria-hidden />
        <span className="hidden xl:inline">Informes</span>
      </button>
      {/* Modo sala: mapa de mesas (fase de restaurante). Solo cuando el puesto
          lo monta — en mostrador puro la vista no existe. */}
      {onFloor && (
        <button type="button" className={BUTTON} onClick={onFloor} title="Sala (mesas)">
          <Armchair className="size-5" aria-hidden />
          <span className="hidden xl:inline">Sala</span>
        </button>
      )}

      <div className="mx-1 h-8 w-px bg-slate-600" aria-hidden />

      <span className="hidden text-sm text-slate-300 sm:inline">{user}</span>
      <button type="button" className={BUTTON} onClick={onLogout} title="Cerrar sesión">
        <LogOut className="size-5" aria-hidden />
      </button>
    </header>
  );
}
