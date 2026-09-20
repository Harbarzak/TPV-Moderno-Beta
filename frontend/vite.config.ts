import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// El API vive en el mismo servidor en producción; en desarrollo se delega por
// proxy para que el frontend llame siempre a rutas relativas /api/v1/...
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', ws: true }, // ws: el hub de la sala (fase 12) vía proxy
    },
  },
  test: {
    environment: 'jsdom',
    css: false,
  },
});
