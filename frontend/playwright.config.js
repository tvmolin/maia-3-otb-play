import { defineConfig, devices } from '@playwright/test';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const repoRoot = fileURLToPath(new URL('..', import.meta.url));
const venvPython = [
  `${repoRoot}.venv/Scripts/python.exe`,
  `${repoRoot}.venv/bin/python`,
].find(existsSync);
const python = process.env.E2E_PYTHON || venvPython || 'python';
const port = 5055;

export default defineConfig({
  testDir: './e2e',
  // The test server holds one game at a time, so tests must run serially.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: 'retain-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: {
    command: `"${python}" tests/e2e_server.py ${port}`,
    cwd: repoRoot,
    url: `http://127.0.0.1:${port}/api/state`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
