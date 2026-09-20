/**
 * Inicio: saludo y atajos a las secciones que ESTE usuario puede ver. Solo
 * organiza navegación — ninguna decisión de negocio.
 */

import { CalendarClock, CreditCard, ListOrdered, Package, ShoppingCart, Utensils } from 'lucide-react';
import { sectionsFor } from '../lib/permissions';
import type { Capability } from '../lib/capabilities';
import type { Me } from '../lib/schemas';
import type { ScreenId } from '../App';

interface DashboardProps {
  me: Me;
  tables: Capability;
  onNavigate: (screen: ScreenId) => void;
}

export default function DashboardScreen({ me, tables, onNavigate }: DashboardProps) {
  const sections = sectionsFor(me.permissions);

  const shortcuts: Array<{ id: ScreenId; label: string; icon: typeof Package; show: boolean }> = [
    { id: 'sales', label: 'Vender', icon: ShoppingCart, show: sections.selling },
    { id: 'orders', label: 'Pedidos', icon: ListOrdered, show: sections.selling },
    { id: 'tables', label: 'Mesas', icon: Utensils, show: tables === 'active' },
    { id: 'products', label: 'Productos', icon: Package, show: sections.products },
    { id: 'stats', label: 'Estadísticas', icon: CalendarClock, show: sections.statsShift || sections.statsHistory },
    { id: 'cash', label: 'Caja', icon: CreditCard, show: sections.cash },
  ];

  const visible = shortcuts.filter((shortcut) => shortcut.show);

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-slate-200 bg-white px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))]">
        <h1 className="text-lg font-semibold text-slate-800">Hola, {me.full_name}</h1>
        <p className="text-sm text-slate-500">{me.role}</p>
      </header>

      <div className="flex-1 overflow-y-auto p-4 pb-[calc(5rem+env(safe-area-inset-bottom))]">
        {visible.length === 0 ? (
          <p className="rounded-xl bg-slate-100 p-4 text-center text-sm text-slate-500">
            Tu usuario no tiene secciones asignadas. Contacta con administración.
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            {visible.map(({ id, label, icon: Icon }) => (
              <button
                key={id}
                type="button"
                onClick={() => onNavigate(id)}
                className="card flex min-h-[88px] flex-col items-start justify-between active:bg-slate-50"
              >
                <Icon size={24} className="text-teal-700" aria-hidden />
                <span className="text-base font-medium text-slate-800">{label}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
