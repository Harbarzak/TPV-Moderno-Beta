/**
 * Shell de la PWA: restauración de sesión al arrancar, acceso si no la hay y
 * navegación inferior según PERMISOS del backend (visibilidad local; la
 * autoridad sigue siendo el servidor). Sin router: una pantalla por estado,
 * suficiente para un móvil de camarero.
 *
 * Offline (fase 14): el estado de conexión se sondea aquí arriba y el banner
 * avisa en todas las pantallas; al volver la conexión se drena el outbox
 * (pedidos abiertos sin red) antes de que el usuario siga tocando.
 */

import { useEffect, useState } from 'react';
import {
  CreditCard,
  LayoutGrid,
  ListOrdered,
  Settings as SettingsIcon,
  ShoppingCart,
  Utensils,
  WifiOff,
} from 'lucide-react';
import { useConnectivity } from './lib/connectivity';
import { useOutbox } from './state/outbox';
import { useSession } from './state/session';
import { sectionsFor } from './lib/permissions';
import { cachedTables, probeTables, type Capability } from './lib/capabilities';
import LoginScreen from './screens/LoginScreen';
import DashboardScreen from './screens/DashboardScreen';
import SalesScreen from './screens/SalesScreen';
import OrdersScreen from './screens/OrdersScreen';
import TablesScreen from './screens/TablesScreen';
import TableOrderScreen from './screens/TableOrderScreen';
import ProductsScreen from './screens/ProductsScreen';
import StatsScreen from './screens/StatsScreen';
import CashScreen from './screens/CashScreen';
import SettingsScreen from './screens/SettingsScreen';

export type ScreenId =
  | 'dashboard'
  | 'sales'
  | 'orders'
  | 'tables'
  | 'products'
  | 'stats'
  | 'cash'
  | 'settings';

interface NavItem {
  id: ScreenId;
  label: string;
  icon: typeof LayoutGrid;
}

export default function App() {
  const { me, ready, restore } = useSession();
  const online = useConnectivity((state) => state.online);
  const initConnectivity = useConnectivity((state) => state.init);
  const outboxPending = useOutbox((state) => state.entries.length);
  const [screen, setScreen] = useState<ScreenId>('dashboard');
  const [tables, setTables] = useState<Capability>(cachedTables);
  // Pedido de mesa en curso (fase 31): {id, etiqueta de mesa} o null para el plano.
  const [tableOrder, setTableOrder] = useState<{ id: string; label: string } | null>(null);

  useEffect(() => {
    void restore();
  }, [restore]);

  // Conectividad (fase 14): sonda inicial + eventos online/offline del
  // navegador + re-sondeo periódico mientras se esté fuera de línea.
  useEffect(() => initConnectivity(), [initConnectivity]);

  // Al volver la conexión se drena la bandeja de salida (en orden, con las
  // Idempotency-Key de cada operación: un reintento repetido no duplica).
  useEffect(() => {
    if (online) void useOutbox.getState().flush();
  }, [online]);

  // «Mesas si están activas»: sonda al entrar con sesión; 404 (fase 16 aún
  // no en el servidor) mantiene la sección oculta sin error al usuario.
  useEffect(() => {
    if (me) void probeTables().then(setTables);
  }, [me]);

  if (!ready) {
    return <div className="grid h-dvh place-items-center text-sm text-slate-400">TPV Móvil…</div>;
  }

  if (!me) {
    return <LoginScreen />;
  }

  const sections = sectionsFor(me.permissions);

  const nav: NavItem[] = [
    { id: 'dashboard', label: 'Inicio', icon: LayoutGrid },
    ...(sections.selling
      ? [
          { id: 'sales' as const, label: 'Venta', icon: ShoppingCart },
          { id: 'orders' as const, label: 'Pedidos', icon: ListOrdered },
        ]
      : []),
    ...(tables === 'active'
      ? [{ id: 'tables' as const, label: 'Mesas', icon: Utensils }]
      : []),
    ...(sections.cash ? [{ id: 'cash' as const, label: 'Caja', icon: CreditCard }] : []),
    { id: 'settings', label: 'Ajustes', icon: SettingsIcon },
  ];

  return (
    <div className="mx-auto flex h-dvh max-w-md flex-col">
      {!online && (
        <div
          role="status"
          className="flex items-center justify-center gap-2 bg-amber-100 px-3 py-2 text-center text-[11px] font-medium leading-tight text-amber-900"
        >
          <WifiOff size={14} aria-hidden />
          <span>
            Sin conexión: puedes seguir añadiendo al ticket
            {outboxPending > 0 ? ` (${outboxPending} pendiente${outboxPending === 1 ? '' : 's'} de enviar)` : ''}.
            Cobrar y anular requieren servidor.
          </span>
        </div>
      )}
      <main className="min-h-0 flex-1">
        {screen === 'dashboard' && <DashboardScreen me={me} tables={tables} onNavigate={setScreen} />}
        {screen === 'sales' && sections.selling && <SalesScreen onNavigate={setScreen} />}
        {screen === 'orders' && sections.selling && <OrdersScreen onNavigate={setScreen} />}
        {screen === 'tables' && tables === 'active' && (tableOrder === null ? (
          <TablesScreen onEnterTable={(id, label) => setTableOrder({ id, label })} />
        ) : (
          <TableOrderScreen
            orderId={tableOrder.id}
            label={tableOrder.label}
            onBack={() => setTableOrder(null)}
          />
        ))}
        {screen === 'products' && <ProductsScreen />}
        {screen === 'stats' && <StatsScreen me={me} />}
        {screen === 'cash' && sections.cash && <CashScreen me={me} />}
        {screen === 'settings' && <SettingsScreen me={me} />}
      </main>

      <nav className="flex border-t border-slate-200 bg-white pb-[env(safe-area-inset-bottom)]">
        {nav.map(({ id, label, icon: Icon }) => {
          const active = screen === id;
          return (
            <button
              key={id}
              type="button"
              onClick={() => {
                setTableOrder(null); // salir del pedido al cambiar de sección
                setScreen(id);
              }}
              aria-current={active ? 'page' : undefined}
              className={`flex min-h-[56px] flex-1 flex-col items-center justify-center gap-0.5 text-[11px] ${
                active ? 'text-teal-700' : 'text-slate-500'
              }`}
            >
              <Icon size={22} aria-hidden />
              {label}
            </button>
          );
        })}
      </nav>
    </div>
  );
}
