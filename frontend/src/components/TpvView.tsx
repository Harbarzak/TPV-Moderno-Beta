/**
 * Pantalla de venta: navegación de paneles + rejilla de productos + ticket.
 * Todo se pinta desde la caché de terminal (§7.2); el lector de códigos
 * añade directamente al ticket. Nunca Three.js (§8): React + Tailwind.
 *
 * Puesto completo (fase 28): terminal configurado (localStorage, patrón del
 * móvil), caja del terminal (GET current / POST open) y cobro real con
 * idempotencia (CheckoutDialog). Los avisos transitorios van por toasts (§6);
 * el banner sigue solo para el estado persistente de conexión/caché.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { CircleAlert, Loader2, WifiOff } from 'lucide-react';
import { attachBarcodeListener } from '../lib/barcode';
import { useConnectivity } from '../lib/connectivity';
import { findByBarcode } from '../lib/search';
import { toCartProduct } from '../lib/product';
import type { PanelItem } from '../lib/schemas';
import { useCart } from '../state/cart';
import { useCatalog } from '../state/catalog';
import { useCash } from '../state/cash';
import { useSession } from '../state/session';
import { useTerminal } from '../state/terminal';
import { toast } from '../state/toasts';
import { useGlobalShortcuts } from '../hooks/useGlobalShortcuts';
import TopBar from './TopBar';
import FloorView from './FloorView';
import PanelNav from './PanelNav';
import PanelGrid from './PanelGrid';
import TicketPanel from './TicketPanel';
import SearchOverlay from './SearchOverlay';
import HelpOverlay from './HelpOverlay';
import ReportsView from './ReportsView';
import Toasts from './Toasts';
import CashDialog from './CashDialog';
import CheckoutDialog from './CheckoutDialog';
import TerminalDialog from './TerminalDialog';

export default function TpvView() {
  const user = useSession((state) => state.user) ?? 'terminal';
  const logout = useSession((state) => state.logout);

  const online = useConnectivity((state) => state.online);
  const initConnectivity = useConnectivity((state) => state.init);
  const status = useCatalog((state) => state.status);
  const error = useCatalog((state) => state.error);
  const stale = useCatalog((state) => state.stale);
  const panels = useCatalog((state) => state.panels);
  const products = useCatalog((state) => state.products);
  const load = useCatalog((state) => state.load);

  const terminal = useTerminal((state) => state.terminal);
  const session = useCash((state) => state.session);
  const loadCash = useCash((state) => state.load);

  const addProduct = useCart((state) => state.addProduct);

  // Vista activa: la venta es la de siempre; «informes» consulta el histórico
  // del servidor (fase 15) y «sala» abre el mapa de mesas (restaurante) sin
  // desmontar el ticket local.
  const [view, setView] = useState<'sale' | 'reports' | 'floor'>('sale');
  const [panelId, setPanelId] = useState<string | null>(null);
  const [subpanelId, setSubpanelId] = useState<string | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [terminalOpen, setTerminalOpen] = useState(false);
  const [cashDialogOpen, setCashDialogOpen] = useState(false);
  const [checkoutOpen, setCheckoutOpen] = useState(false);

  // Carga inicial de la caché de terminal.
  useEffect(() => {
    void load();
  }, [load]);

  // Conectividad (fase 14): sonda inicial + eventos del navegador + re-sondeo
  // periódico mientras no haya servidor.
  useEffect(() => initConnectivity(), [initConnectivity]);

  // Catálogo pintado desde la copia guardada: en cuanto el servidor escuche,
  // se refresca solo (y ``stale`` cae al validar la carga buena).
  useEffect(() => {
    if (online && stale) void load(true);
  }, [online, stale, load]);

  // Caja del terminal: se consulta al haber terminal y conexión (y en cada
  // reconexión, por si se abrió o cerró desde el móvil).
  useEffect(() => {
    if (terminal && online) void loadCash(terminal.id);
  }, [terminal, online, loadCash]);

  // El primer panel queda seleccionado al llegar el árbol.
  useEffect(() => {
    if (panelId === null && panels.length > 0) setPanelId(panels[0].id);
  }, [panels, panelId]);

  const pickItem = useCallback(
    (item: PanelItem) => addProduct(toCartProduct(item.product)),
    [addProduct],
  );

  // Lector de códigos global: pasa un código → directo al ticket. Solo en la
  // vista de venta: en informes un escaneo no debe tocar el ticket.
  useEffect(() => {
    if (view !== 'sale') return;
    const detach = attachBarcodeListener(window, (code) => {
      const product = findByBarcode(products, code);
      if (product) {
        addProduct(toCartProduct(product));
        toast.success(`${product.name} añadido`, { key: 'barcode' });
      } else {
        toast.error(`Código no encontrado: ${code}`, { key: 'barcode' });
      }
    });
    return detach;
  }, [view, products, addProduct]);

  // El cobro guía al puesto: sin terminal → configurarlo; sin caja → abrirla;
  // con ambos → el diálogo de cobro (que a su vez exige conexión).
  const openCheckout = useCallback(() => {
    if (view !== 'sale') return;
    if (useCart.getState().lines.length === 0) {
      toast.info('El ticket está vacío: añade productos antes de cobrar', { key: 'checkout' });
      return;
    }
    if (!useTerminal.getState().terminal) {
      toast.info('Configura primero el terminal de este puesto', { key: 'checkout' });
      setTerminalOpen(true);
      return;
    }
    if (useCash.getState().session?.status !== 'open') {
      toast.info('Abre la caja antes de cobrar', { key: 'checkout' });
      setCashDialogOpen(true);
      return;
    }
    setCheckoutOpen(true);
  }, [view]);

  const shortcuts = useMemo(
    () => ({
      help: () => setHelpOpen(true),
      search: () => {
        setView('sale');
        setSearchOpen(true);
      },
      checkout: openCheckout,
    }),
    [openCheckout],
  );
  useGlobalShortcuts(shortcuts);

  const panel = panels.find((candidate) => candidate.id === panelId) ?? null;
  const activeItems: PanelItem[] = panel
    ? subpanelId === null
      ? panel.items
      : (panel.subpanels.find((sub) => sub.id === subpanelId)?.items ?? [])
    : [];

  const selectPanel = (nextId: string) => {
    setPanelId(nextId);
    setSubpanelId(null);
  };

  return (
    <div className="flex h-screen flex-col bg-slate-900 text-slate-100">
      <TopBar
        user={user}
        online={online}
        terminalLabel={terminal?.label ?? null}
        cashOpen={session?.status === 'open'}
        cashLabel={session?.status === 'open' ? `· ${session.opening_amount} €` : null}
        onTerminal={() => setTerminalOpen(true)}
        onCash={() => setCashDialogOpen(true)}
        onSearch={() => {
          setView('sale');
          setSearchOpen(true);
        }}
        onHelp={() => setHelpOpen(true)}
        onReports={() => setView('reports')}
        onFloor={() => setView('floor')}
        onReload={() => void load(true)}
        onLogout={logout}
      />

      {(!online || stale) && (
        <div
          role="status"
          className="flex items-center justify-center gap-2 bg-amber-400 px-3 py-1.5 text-sm font-medium text-slate-900"
        >
          <WifiOff size={16} aria-hidden />
          <span>
            {!online
              ? 'Sin conexión con el servidor: la venta sigue con el catálogo y el ticket guardados.'
              : 'Catálogo mostrado desde la copia guardada; actualizando…'}
          </span>
        </div>
      )}

      {view === 'reports' ? (
        <ReportsView onBack={() => setView('sale')} />
      ) : view === 'floor' ? (
        // El plano va antes de las pantallas de catálogo: funciona incluso
        // mientras la caché de terminal aún carga (los paneles llegan luego).
        <FloorView panels={panels} onBack={() => setView('sale')} />
      ) : status === 'loading' || status === 'idle' ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-slate-400">
          <Loader2 className="size-10 animate-spin" aria-hidden />
          <p className="text-lg">Cargando catálogo…</p>
        </div>
      ) : status === 'error' ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6 text-center">
          <CircleAlert className="size-12 text-red-400" aria-hidden />
          <p className="text-lg text-slate-200">No se pudo cargar el catálogo</p>
          <p className="text-sm text-slate-400">{error}</p>
          <button
            type="button"
            onClick={() => void load(true)}
            className="rounded-lg bg-amber-500 px-6 py-3 font-semibold text-slate-900 hover:bg-amber-400"
          >
            Reintentar
          </button>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <PanelNav
            panels={panels}
            selectedPanelId={panelId}
            selectedSubpanelId={subpanelId}
            onSelectPanel={selectPanel}
            onSelectSubpanel={setSubpanelId}
          />
          <main className="flex min-w-0 flex-1 flex-col bg-slate-900">
            <PanelGrid items={activeItems} onPick={pickItem} />
          </main>
          <TicketPanel onCheckout={openCheckout} />
        </div>
      )}

      {searchOpen && <SearchOverlay products={products} onClose={() => setSearchOpen(false)} />}
      {helpOpen && <HelpOverlay onClose={() => setHelpOpen(false)} />}
      {terminalOpen && <TerminalDialog onClose={() => setTerminalOpen(false)} />}
      {cashDialogOpen && <CashDialog onClose={() => setCashDialogOpen(false)} />}
      {checkoutOpen && <CheckoutDialog onClose={() => setCheckoutOpen(false)} />}

      <Toasts />
    </div>
  );
}
