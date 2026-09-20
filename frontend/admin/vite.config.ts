import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Panel de administración: MISMA API que el TPV de mostrador (§2), rutas
// relativas /api/v1/... delegadas por proxy en desarrollo. Puerto propio
// (5175) para poder tener mostrador, móvil y admin abiertos a la vez.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    proxy: {
      '/api': { target: 'http://localhost:8000' },
    },
  },
  test: {
    environment: 'jsdom',
    css: false,
  },
});
