import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  base: '/static/chessground/',
  build: {
    emptyOutDir: true,
    outDir: '../static/chessground',
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL('./main.js', import.meta.url)),
        profile: fileURLToPath(new URL('./profile.js', import.meta.url)),
      },
      output: {
        assetFileNames: 'assets/[name][extname]',
        chunkFileNames: 'assets/[name].js',
        entryFileNames: (chunk) =>
          chunk.name === 'main' ? 'assets/app.js' : 'assets/[name].js',
      },
    },
  },
});
