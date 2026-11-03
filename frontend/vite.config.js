import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  base: '/static/chessground/',
  build: {
    emptyOutDir: true,
    outDir: '../static/chessground',
    rollupOptions: {
      input: fileURLToPath(new URL('./main.js', import.meta.url)),
      output: {
        assetFileNames: 'assets/[name][extname]',
        chunkFileNames: 'assets/[name].js',
        entryFileNames: 'assets/app.js',
      },
    },
  },
});
