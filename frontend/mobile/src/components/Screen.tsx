/**
 * Contenedor de pantalla: título + acción derecha opcional + cuerpo con
 * scroll propio. Las pantallas no gestionan layout de shell (eso es App).
 */

import type { ReactNode } from 'react';

interface ScreenProps {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}

export default function Screen({ title, action, children }: ScreenProps) {
  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between gap-2 border-b border-slate-200 bg-white px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))]">
        <h1 className="text-lg font-semibold text-slate-800">{title}</h1>
        {action}
      </header>
      <div className="flex-1 overflow-y-auto p-4 pb-[calc(5rem+env(safe-area-inset-bottom))]">
        {children}
      </div>
    </div>
  );
}
