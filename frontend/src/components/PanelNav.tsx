/**
 * Navegación Categorías → Paneles → SubPaneles (columna izquierda). Cada fila
 * es un panel; al seleccionarlo se despliegan sus subpaneles con "Principal"
 * delante (los botones directos del panel).
 */

import type { Panel } from '../lib/schemas';

interface PanelNavProps {
  panels: Panel[];
  selectedPanelId: string | null;
  selectedSubpanelId: string | null;
  onSelectPanel: (panelId: string) => void;
  onSelectSubpanel: (subpanelId: string | null) => void;
}

const ITEM =
  'w-full rounded-lg px-4 py-3 text-left text-base font-medium transition ' +
  'active:scale-[0.99] disabled:cursor-not-allowed';

export default function PanelNav({
  panels,
  selectedPanelId,
  selectedSubpanelId,
  onSelectPanel,
  onSelectSubpanel,
}: PanelNavProps) {
  const selected = panels.find((panel) => panel.id === selectedPanelId) ?? null;

  return (
    <nav aria-label="Paneles" className="flex w-56 shrink-0 flex-col gap-1 overflow-y-auto bg-slate-800 p-3">
      {panels.map((panel) => (
        <div key={panel.id}>
          <button
            type="button"
            onClick={() => onSelectPanel(panel.id)}
            aria-current={panel.id === selectedPanelId}
            className={`${ITEM} ${
              panel.id === selectedPanelId
                ? 'bg-amber-500 text-slate-900'
                : 'bg-slate-700 text-slate-100 hover:bg-slate-600'
            }`}
          >
            {panel.name}
          </button>

          {panel.id === selectedPanelId && panel.subpanels.length > 0 && (
            <div className="ml-4 mt-1 flex flex-col gap-1 border-l-2 border-slate-600 pl-2">
              <button
                type="button"
                onClick={() => onSelectSubpanel(null)}
                className={`${ITEM} py-2 text-sm ${
                  selectedSubpanelId === null
                    ? 'bg-slate-600 text-amber-300'
                    : 'bg-slate-700/60 text-slate-300 hover:bg-slate-600'
                }`}
              >
                Principal
              </button>
              {panel.subpanels.map((sub) => (
                <button
                  key={sub.id}
                  type="button"
                  onClick={() => onSelectSubpanel(sub.id)}
                  className={`${ITEM} py-2 text-sm ${
                    sub.id === selectedSubpanelId
                      ? 'bg-slate-600 text-amber-300'
                      : 'bg-slate-700/60 text-slate-300 hover:bg-slate-600'
                  }`}
                >
                  {sub.name}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
      {selected === null && (
        <p className="px-2 py-6 text-sm text-slate-400">
          Sin paneles. Se configuran desde la API de catálogo.
        </p>
      )}
    </nav>
  );
}
