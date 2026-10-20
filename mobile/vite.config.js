import { defineConfig } from 'vite';

export default defineConfig({
  root: 'src',
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: 'src/index.html'
      }
    }
  },
  server: {
    port: 3000,
    host: '0.0.0.0'
  }
});
