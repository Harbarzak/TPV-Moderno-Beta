/**
 * Atajos globales de la pantalla de venta (F1 ayuda, F2 búsqueda, F4 cobro).
 * Los lectores y el tacto no los tocan; se capturan en window para funcionar
 * sea cual sea el foco, salvo cuando un diálogo de Radix ya consume la tecla.
 */

import { useEffect } from 'react';

export interface ShortcutHandlers {
  help: () => void;
  search: () => void;
  checkout: () => void;
}

export function useGlobalShortcuts(handlers: ShortcutHandlers): void {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'F1') {
        event.preventDefault();
        handlers.help();
      } else if (event.key === 'F2') {
        event.preventDefault();
        handlers.search();
      } else if (event.key === 'F4') {
        event.preventDefault();
        handlers.checkout();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [handlers]);
}
