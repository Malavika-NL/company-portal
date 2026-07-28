import { spawn, type ChildProcess } from 'node:child_process';
import { createConnection } from 'node:net';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

const backendDirectory = resolve(dirname(fileURLToPath(import.meta.url)), '../backend');
// This is the SSO exchange endpoint configured in SalesPie and BDCRM.
const portalBackendPort = 8004;

function portalStatus(): Plugin {
  return {
    name: 'company-portal-status',
    configureServer(server) {
      server.middlewares.use('/__company-portal/status', (_request, response) => {
        response.statusCode = 200;
        response.setHeader('Content-Type', 'application/json');
        response.end('{"service":"company-portal"}');
      });
    },
  };
}

const backendIsRunning = () => new Promise<boolean>((complete) => {
  const socket = createConnection({ host: '127.0.0.1', port: portalBackendPort });
  socket.setTimeout(500);
  socket.once('connect', () => { socket.destroy(); complete(true); });
  socket.once('timeout', () => { socket.destroy(); complete(false); });
  socket.once('error', () => complete(false));
});

function supervisePortalBackend(): Plugin {
  let backend: ChildProcess | null = null;
  let closing = false;
  let restartTimer: ReturnType<typeof setTimeout> | null = null;

  const startBackend = async () => {
    if (closing || backend || await backendIsRunning()) return;

    backend = spawn(
      // On Windows, `python.exe` can be the Microsoft Store app-execution
      // alias even when Python is installed. The Python launcher resolves the
      // actual installed interpreter instead.
      process.platform === 'win32' ? 'py.exe' : 'python',
      ['manage.py', 'runserver', `127.0.0.1:${portalBackendPort}`, '--noreload'],
      {
        cwd: backendDirectory,
        env: { ...process.env, DJANGO_SETTINGS_MODULE: 'portal.settings' },
        stdio: ['ignore', 'inherit', 'inherit'],
        windowsHide: true,
      },
    );

    backend.once('error', (error) => {
      console.error('[portal-backend] Unable to start:', error.message);
    });
    backend.once('exit', () => {
      backend = null;
      if (!closing) restartTimer = setTimeout(() => void startBackend(), 1500);
    });
  };

  return {
    name: 'company-portal-backend-supervisor',
    configureServer(server) {
      closing = false;
      void startBackend();
      server.httpServer?.once('close', () => {
        closing = true;
        if (restartTimer) clearTimeout(restartTimer);
        backend?.kill();
        backend = null;
      });
    },
  };
}

export default defineConfig({
  plugins: [react(), portalStatus(), supervisePortalBackend()],
  server: {
    // Listen on every local interface so devices on the LAN can use the portal.
    host: '0.0.0.0',
    port: 8002,
    strictPort: true,
    proxy: { '/api': { target: `http://127.0.0.1:${portalBackendPort}`, changeOrigin: true } },
  },
});
