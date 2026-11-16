import { defineConfig } from "vite";
import { svelte, vitePreprocess } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";

// Spike: build directo al directorio de estáticos de FastAPI.
// `base` hace que los assets apunten a /static/calendar/... tal cual los
// sirve el mount existente de app.main.
export default defineConfig({
  plugins: [tailwindcss(), svelte({ preprocess: vitePreprocess() })],
  base: "/static/calendar/",
  build: {
    outDir: "../app/static/calendar",
    emptyOutDir: true,
    assetsDir: "assets",
  },
  server: {
    proxy: {
      // En dev (`npm run dev`), la API real corre en :8000 (uvicorn).
      "/api": "http://localhost:8000",
    },
  },
});
