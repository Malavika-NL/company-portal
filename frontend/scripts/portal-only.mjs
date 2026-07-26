// Disable CRM warm-up before the Vite backend supervisor starts Django.
// This must be set before dev.mjs loads the Vite configuration.
process.env.PORTAL_AUTO_START_CRMS = 'false';
process.env.PORTAL_AUTO_START_CRMS_ON_BOOT = 'false';

await import('./dev.mjs');
