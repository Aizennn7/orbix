import { defineConfig } from 'vite';
import { resolve } from 'path';

// Base path for GitHub Pages: https://Aizennn7.github.io/orbix/
// Set via env: VITE_BASE=/orbix/ for Pages, default / for local dev
const base = process.env.VITE_BASE || process.env.BASE || '/';

export default defineConfig({
  root: 'src',
  base: base,
  publicDir: resolve(__dirname, '../public'),
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'src/index.html')
      }
    }
  },
  server: {
    port: 3000,
    host: '0.0.0.0'
  }
});
