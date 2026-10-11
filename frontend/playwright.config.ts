import { randomUUID } from 'node:crypto';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';

const state = path.join(tmpdir(), `atlas-ui-${randomUUID()}`).replaceAll('\\', '/');
const external = process.env.ATLAS_E2E_URL;

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  workers: 1,
  use: {
    baseURL: external || 'http://127.0.0.1:8000',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    launchOptions: process.env.ATLAS_E2E_CHROMIUM_PATH
      ? {
          executablePath: process.env.ATLAS_E2E_CHROMIUM_PATH,
          args: [
            '--no-sandbox',
            '--no-zygote',
            '--single-process',
            '--disable-gpu',
            '--disable-software-rasterizer',
            '--use-gl=disabled',
          ],
        }
      : {},
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 1050 } } },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 } } },
  ],
  webServer: external
    ? undefined
    : {
        command: 'python -m atlas.cli serve --port 8000',
        cwd: path.resolve(import.meta.dirname, '..'),
        url: 'http://127.0.0.1:8000/readyz',
        reuseExistingServer: false,
        env: {
          ATLAS_BACKEND: 'demo',
          ATLAS_SANDBOX: 'local',
          ATLAS_ALLOW_TRUSTED_LOCAL: 'true',
          ATLAS_ENVIRONMENT: 'development',
          ATLAS_API_KEY: '',
          ATLAS_DATABASE_URL: `sqlite:///${state}/atlas.db`,
          ATLAS_DATA_DIR: `${state}/data`,
          ATLAS_REPO_ROOT: `${state}/repositories`,
        },
      },
});
