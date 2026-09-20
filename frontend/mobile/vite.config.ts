import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Misma API que el TPV de mostrador (§2: la PWA habla con el mismo servidor):
// rutas relativas /api/v1/... delegadas por proxy en desarrollo. Puerto propio
// (5174) para poder tener ambos clientes abiertos a la vez.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/api': { target: 'http://localhost:8000' },
    },
  },
  test: {
    environment: 'jsdom',
    css: false,
  },
});
