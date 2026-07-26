import { createServer } from 'vite';
import { get } from 'node:http';
import { createConnection } from 'node:net';

const portalUrl = 'http://127.0.0.1:5176';

const isPortOpen = () => new Promise((complete) => {
  const socket = createConnection({ host: '127.0.0.1', port: 5176 });
  socket.setTimeout(500);
  socket.once('connect', () => { socket.destroy(); complete(true); });
  socket.once('timeout', () => { socket.destroy(); complete(false); });
  socket.once('error', () => complete(false));
});

const isPortalServer = () => new Promise((complete) => {
  const request = get(`${portalUrl}/__company-portal/status`, { timeout: 1000 }, (response) => {
    let body = '';
    response.setEncoding('utf8');
    response.on('data', (chunk) => { body += chunk; });
    response.on('end', () => complete(response.statusCode === 200 && body.includes('company-portal')));
  });
  request.once('timeout', () => { request.destroy(); complete(false); });
  request.once('error', () => complete(false));
});

const explainExistingServer = async () => {
  if (await isPortalServer()) {
    console.log(`\nCompany Portal is already running at ${portalUrl}`);
    console.log('Open that address in your browser. A second portal server was not started.');
  } else {
    console.log(`\nPort 5176 is being used by another application.`);
    console.log('Close that application, then run npm run dev again.');
  }
};

let server;

if (await isPortOpen()) {
  await explainExistingServer();
} else {
  try {
    server = await createServer({ clearScreen: false });
    await server.listen();
    server.printUrls();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const portIsInUse = (error && error.code === 'EADDRINUSE')
      || /port\s+5176\s+is\s+already\s+in\s+use/i.test(message);

    if (portIsInUse) {
      await explainExistingServer();
    } else {
      console.error('\nCompany Portal could not start.');
      console.error(message);
      process.exitCode = 1;
    }
  }
}

if (server) {
  const stop = async () => {
    await server.close();
    process.exit(0);
  };

  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
}
