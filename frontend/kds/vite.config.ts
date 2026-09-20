import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Cuarta SPA (fase 32): el KDS vive aparte del TPV de mostrador y del móvil —
// pantalla de cocina a pantalla completa, sin nada de negocio de venta.
// Puerto propio (5175) para poder tener los tres clientes abiertos a la vez;
// el proxy de /api también lleva WebSocket (ws: true): el hub vive en la misma
// ruta /api/v1/ws.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    proxy: {
      '/api': { target: 'http://localhost:8000', ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    css: false,
  },
});
