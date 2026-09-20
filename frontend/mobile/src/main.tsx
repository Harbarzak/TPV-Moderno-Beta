import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

// El service worker (shell instalable, API nunca cacheada) solo en producción:
// en desarrollo estorbaría con el recargado de Vite.
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    // Ruta relativa: el servidor puede publicar la PWA bajo /app/movil
    // (fase Instalador); en desarrollo, servido desde la raíz, es equivalente.
    navigator.serviceWorker.register('./sw.js').catch(() => {
      // Sin SW la app sigue funcionando online; no es error fatal.
    });
  });
}
