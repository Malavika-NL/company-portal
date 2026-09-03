import { spawn } from 'node:child_process';
import { createConnection } from 'node:net';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const crmKey = process.argv[2];
const portalFrontend = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const workspaceHome = resolve(portalFrontend, '..', '..');
// Prefer the Windows Python launcher: `python.exe` may only be the
// Microsoft Store app-execution alias.
const python = process.platform === 'win32' ? 'py.exe' : 'python';
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';

const crms = {
  marketing: {
    name: 'Marketing CRM',
    backend: { port: 8000, cwd: resolve(workspaceHome, 'email_campaign_project-4', 'backend'), args: ['manage.py', 'runserver', '127.0.0.1:8000', '--noreload'] },
    frontend: { port: 5173, cwd: resolve(workspaceHome, 'email_campaign_project-4', 'frontend') },
  },
  salespie: {
    name: 'SalesPie',
    backend: { port: 8001, cwd: resolve(workspaceHome, 'SalesPie', 'backend'), args: ['manage.py', 'runserver', '127.0.0.1:8001', '--noreload'] },
    frontend: { port: 5174, cwd: resolve(workspaceHome, 'SalesPie', 'frontend') },
  },
  bdcrm: {
    name: 'BDCRM',
    backend: { port: 8003, cwd: resolve(workspaceHome, 'BDCRM-1', 'BDCRM', 'bdcrm'), args: ['manage.py', 'runserver', '127.0.0.1:8003', '--noreload'] },
    frontend: { port: 5175, cwd: resolve(workspaceHome, 'BDCRM-1', 'frontend') },
  },
};

const crm = crms[crmKey];
if (!crm) {
  console.error('Choose one CRM: marketing, salespie, or bdcrm.');
  process.exitCode = 1;
} else {
  const isPortOpen = (port) => new Promise((complete) => {
    const socket = createConnection({ host: '127.0.0.1', port });
    socket.setTimeout(500);
    socket.once('connect', () => { socket.destroy(); complete(true); });
    socket.once('timeout', () => { socket.destroy(); complete(false); });
    socket.once('error', () => complete(false));
  });

  const children = [];
  const start = (label, command, args, cwd) => {
    console.log(`Starting ${crm.name} ${label}...`);
    const child = spawn(command, args, {
      cwd,
      stdio: 'inherit',
      windowsHide: true,
    });
    child.once('error', (error) => console.error(`${crm.name} ${label} could not start: ${error.message}`));
    children.push(child);
  };

  const backendRunning = await isPortOpen(crm.backend.port);
  const frontendRunning = await isPortOpen(crm.frontend.port);

  if (!backendRunning) start('backend', python, crm.backend.args, crm.backend.cwd);
  if (!frontendRunning) {
    start('frontend', npm, ['run', 'dev', '--', '--host', '127.0.0.1', '--port', String(crm.frontend.port)], crm.frontend.cwd);
  }

  if (!children.length) {
    console.log(`${crm.name} is already running at http://127.0.0.1:${crm.frontend.port}`);
  } else {
    console.log(`${crm.name} will be available at http://127.0.0.1:${crm.frontend.port}`);
  }

  const stop = () => {
    for (const child of children) child.kill();
    process.exit(0);
  };
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
}
