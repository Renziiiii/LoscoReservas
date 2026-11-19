import { defineConfig } from "@playwright/test";

/**
 * E2E con el Chromium del SISTEMA (/usr/bin/chromium — Arch): Playwright no
 * descarga ningún navegador (sin `playwright install`). `executablePath`
 * apunta al binario real; el warning de versión soportada es esperable.
 *
 * El backend FastAPI NO corre: la API se mockea con page.route en cada
 * test (ver e2e/telefono.spec.ts). Se usa el DEV server de Vite porque
 * `base: "/static/calendar/"` hace que `vite preview` (que sirve el outDir
 * ../app/static/calendar como raíz) rompa las URLs de assets.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  use: {
    baseURL: "http://localhost:5173",
    launchOptions: { executablePath: "/usr/bin/chromium" },
    viewport: { width: 1280, height: 800 },
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npx vite --port 5173 --strictPort",
    port: 5173,
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
