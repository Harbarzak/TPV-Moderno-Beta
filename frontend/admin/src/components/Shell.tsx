/**
 * Estructura del panel: navegación lateral con las secciones que el
 * backend permite (visibilidad local; el 403 del servidor manda), cabecera
 * con la identidad y salida. La separación administración / operación
 * diaria es también visual: tema claro índigo frente al mostrador oscuro.
 */

import { useState, type ReactNode } from 'react';
import {
  Boxes,
  Building2,
  ClipboardList,
  FolderTree,
  HardDrive,
  LayoutGrid,
  Layers,
  LogOut,
  Monitor,
  Printer,
  ShieldCheck,
  Tags,
  UserCog,
  Users,
  Wallet,
} from 'lucide-react';
import type { SectionDef } from '../lib/permissions';
import type { Me } from '../lib/schemas';

const ICONS: Record<string, typeof LayoutGrid> = {
  productos: Boxes,
  categorias: Tags,
  departamentos: FolderTree,
  paneles: Layers,
  usuarios: Users,
  permisos: ShieldCheck,
  camareros: UserCog,
  'formas-pago': Wallet,
  terminales: Monitor,
  dispositivos: HardDrive,
  impresoras: Printer,
  configuracion: Building2,
  backups: ClipboardList,
  auditoria: ClipboardList,
};

/** Grupos de la barra lateral (orden fijo, catálogo primero). */
const GROUPS: Array<{ label: string; ids: string[] }> = [
  { label: 'Catálogo', ids: ['productos', 'categorias', 'departamentos', 'paneles'] },
  { label: 'Personal y acceso', ids: ['usuarios', 'camareros', 'permisos'] },
  { label: 'Operativa', ids: ['formas-pago', 'terminales', 'dispositivos', 'impresoras'] },
  { label: 'Sistema', ids: ['configuracion', 'backups', 'auditoria'] },
];

export default function Shell({ me, sections, active, onSelect, onLogout, children }: {
  me: Me;
  sections: SectionDef[];
  active: string | null;
  onSelect: (id: string) => void;
  onLogout: () => void;
  children: ReactNode;
}) {
  const [menuOpen, setMenuOpen] = useState(false);

  // En pantallas estrechas la barra lateral se colapsa al elegir sección.
  const selectAndClose = (id: string) => {
    setMenuOpen(false);
    onSelect(id);
  };

  const nav = (
    <nav className="flex flex-col gap-4 p-3" aria-label="Secciones de administración">
      {GROUPS.map((group) => {
        const items = group.ids
          .map((id) => sections.find((section) => section.id === id))
          .filter((section): section is SectionDef => Boolean(section));
        if (items.length === 0) return null;
        return (
          <div key={group.label}>
            <p className="mb-1 px-2 text-[10px] font-bold uppercase tracking-widest text-slate-400">
              {group.label}
            </p>
            {items.map((section) => {
              const Icon = ICONS[section.id] ?? LayoutGrid;
              const isActive = section.id === active;
              return (
                <button
                  key={section.id}
                  type="button"
                  onClick={() => selectAndClose(section.id)}
                  aria-current={isActive ? 'page' : undefined}
                  className={`mb-0.5 flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition ${
                    isActive
                      ? 'bg-indigo-600 text-white'
                      : 'text-slate-600 hover:bg-indigo-50 hover:text-indigo-700'
                  }`}
                >
                  <Icon size={16} aria-hidden />
                  {section.label}
                </button>
              );
            })}
          </div>
        );
      })}
    </nav>
  );

  return (
    <div className="flex h-dvh">
      <aside className="hidden w-60 shrink-0 flex-col overflow-y-auto border-r border-slate-200 bg-white md:flex">
        <div className="flex items-center gap-2 border-b border-slate-200 px-4 py-4">
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-indigo-600 text-xs font-bold text-white">
            TPV
          </div>
          <div>
            <p className="text-sm font-bold leading-tight text-slate-800">Administración</p>
            <p className="text-[11px] text-slate-400">Panel de gestión</p>
          </div>
        </div>
        {nav}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="btn-secondary !px-2 md:hidden"
              onClick={() => setMenuOpen((open) => !open)}
              aria-label="Abrir menú"
            >
              ☰
            </button>
            <h1 className="hidden text-sm font-semibold text-slate-500 sm:block">
              {sections.find((section) => section.id === active)?.label ?? 'Administración'}
            </h1>
          </div>
          <div className="flex items-center gap-3">
            <div className="text-right">
              <p className="text-sm font-semibold leading-tight text-slate-800">{me.full_name}</p>
              <p className="text-[11px] text-slate-400">
                {me.username} · rol {me.role}
              </p>
            </div>
            <button type="button" onClick={onLogout} className="btn-secondary">
              <LogOut size={15} aria-hidden />
              Salir
            </button>
          </div>
        </header>

        {menuOpen && (
          <div className="border-b border-slate-200 bg-white md:hidden">{nav}</div>
        )}

        <main className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
          <div className="mx-auto max-w-5xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
