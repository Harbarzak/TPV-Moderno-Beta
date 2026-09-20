/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // Design System (docs/design-system.md §3.1): tokens semánticos del
      // mostrador. Son alias de la paleta slate/amber ya en uso — las clases
      // antiguas siguen funcionando; las pantallas nuevas usan el token.
      colors: {
        surface: '#0f172a', // slate-900 · fondo de pantalla
        'surface-raised': '#1e293b', // slate-800 · tarjetas, ticket, header
        'surface-sunken': '#334155', // slate-700 · huecos, teclas, filas
        'surface-hover': '#475569', // slate-600 · hover sobre sunken
        ink: '#f1f5f9', // slate-100 · texto principal
        'ink-muted': '#94a3b8', // slate-400 · texto secundario
        accent: '#f59e0b', // amber-500 · acción principal
        'accent-hover': '#fbbf24', // amber-400 · hover/foco
        'accent-ink': '#020617', // slate-950 · texto sobre accent
        success: '#10b981', // emerald-500
        warning: '#f59e0b', // amber-500 (siempre con texto slate-950)
        danger: '#dc2626', // red-600 · superficie
        'danger-text': '#ef4444', // red-500 · texto sobre superficie oscura
        info: '#0ea5e9', // sky-500
      },
      // Z-index (§3.5): banner offline 60 por encima del contenido; toasts 70.
      zIndex: {
        banner: '60',
        toast: '70',
      },
    },
  },
  plugins: [],
};
