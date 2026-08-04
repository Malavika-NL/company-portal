// Run a local Vite frontend alongside the Docker-hosted portal. The Docker
// backend remains on 8004 and can resolve the CRM services on their networks.
process.env.PORTAL_AUTO_START_CRMS = 'false';
process.env.PORTAL_EXTERNAL_BACKEND = 'true';
process.env.PORTAL_VITE_HOST = '127.0.0.1';
process.env.PORTAL_VITE_PORT = '8012';

await import('./dev.mjs');
