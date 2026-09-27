// Independent config: existing frontend source/config remains unchanged.
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
const root = fileURLToPath(new URL('../frontend/', import.meta.url));
const requireFrontend = createRequire(new URL('../frontend/package.json', import.meta.url));
const { default: react } = await import(pathToFileURL(requireFrontend.resolve('@vitejs/plugin-react')).href);
export default {
  root,
  plugins: [react()],
  server: {
    host: '127.0.0.1', port: 5174, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8001' } },
  },
};
