<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import CalendarioReservas from "./lib/CalendarioReservas.svelte";
  import { COLORES_ESTADO, type Reserva } from "./lib/reservas";
  // Look vanilla de FullCalendar (como los demos): theme forma + paleta blue.
  // Estos imports van DESPUÉS de app.css (ver app.css: cascade a propósito).
  import "fullcalendar/skeleton.css";
  import "fullcalendar/themes/forma/theme.css";
  import "fullcalendar/themes/forma/palettes/blue.css";

  let reservas = $state<Reserva[]>([]);
  let fuente = $state("cargando…");
  let log = $state<{ t: string; msg: string }[]>([]);
  let seleccionada = $state<Reserva | null>(null);

  let rangoActual = $state<{ inicio: string; fin: string } | null>(null);
  /** true cuando el origen de datos es la API (no el demo): habilita escritura. */
  let usandoAPI = $state(false);
  const POLL_MS = 20_000;
  let pollTimer: ReturnType<typeof setInterval> | undefined;

  async function cargarReservas() {
    // Rango visible ampliado (±2 meses alrededor) para cubrir navegación
    // rápida entre vistas antes del próximo datesSet.
    const params = new URLSearchParams();
    if (rangoActual) {
      const ini = new Date(rangoActual.inicio);
      ini.setMonth(ini.getMonth() - 2);
      const fin = new Date(rangoActual.fin);
      fin.setMonth(fin.getMonth() + 2);
      const iso = (d: Date) => d.toISOString().slice(0, 10);
      params.set("fecha_desde", iso(ini));
      params.set("fecha_hasta", iso(fin));
    }
    try {
      const res = await fetch(`/api/reservas?${params}`);
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          reservas = data;
          usandoAPI = true;
          fuente = data.length > 0 ? `API real (${data.length} reservas)` : "API real (0 reservas)";
        }
      }
    } catch {
      fuente = "sin conexión con la API";
    }
  }

  /** Persiste un cambio en la API cuando los datos son reales. */
  async function guardarCambio(id: number, patch: Record<string, string>) {
    if (!usandoAPI) return;
    try {
      const res = await fetch(`/api/reservas/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      anotar(res.ok ? `reserva #${id} guardada` : `PATCH reserva #${id} falló (${res.status})`);
    } catch {
      anotar(`PATCH reserva #${id} sin conexión`);
    }
  }

  function anotar(msg: string) {
    const t = new Date().toLocaleTimeString("es-AR");
    log = [{ t, msg }, ...log].slice(0, 40);
  }

  onMount(async () => {
    await cargarReservas();
    // (2) Polling: todos en la oficina ven cambios sin tocar nada.
    pollTimer = setInterval(cargarReservas, POLL_MS);
  });

  // Limpieza del polling al desmontar (evita fetches zombis en dev con HMR).
  onDestroy(() => clearInterval(pollTimer));
</script>

<header class="flex items-baseline gap-3 px-5 py-3 border-b border-zinc-200">
  <span class="text-xl font-semibold tracking-wide">Losco</span>
  <span class="text-sm text-zinc-500">Calendario de reservas — FullCalendar 7 + Svelte 5 + Tailwind</span>
  <span class="ml-auto text-xs font-medium rounded-full px-2.5 py-1 bg-blue-50 text-blue-700">{fuente}</span>
</header>

<div class="flex flex-wrap items-center gap-x-4 gap-y-1 px-5 py-2 border-b border-zinc-100">
  {#each Object.entries(COLORES_ESTADO) as [estado, c]}
    <span class="inline-flex items-center gap-1.5 text-xs text-zinc-600">
      <i class="w-3 h-3 rounded-sm inline-block" style="background: {c.bg}"></i>{c.label}
    </span>
  {/each}
  <span class="text-xs text-zinc-400 italic">
    drag para reprogramar · estirar para cambiar horario · click en día → alta · click en header del día → vista diaria
  </span>
</div>

<main class="grid grid-cols-1 lg:grid-cols-4 gap-4 p-5 items-start">
  <div class="lg:col-span-3">
    <CalendarioReservas
    {reservas}
    onDatesSet={(inicio, fin) => {
      // (1) Refetch al navegar: cada cambio de vista/fecha trae el rango fresco.
      rangoActual = { inicio, fin };
      cargarReservas();
    }}
    onDateClick={(f) => anotar(`alta de reserva para ${f} (abriría el form precargado)`)}
    onDateSelect={(ini, fin) => anotar(`selección ${ini} → ${fin} (abriría el form con ese rango)`)}
    onEventClick={(r) => (seleccionada = r)}
    onEventDrop={(r, f, h) => {
      anotar(`reserva #${r.id} "${r.nombre}" → ${f} ${h}`);
      guardarCambio(r.id, { fecha: f, hora: h });
    }}
    onEventResize={(r, h) => anotar(`reserva #${r.id} "${r.nombre}" ahora termina ${h} (sin persistir: la reserva no tiene hora de fin)`)}
    />
  </div>

  <aside class="space-y-4">
    {#if seleccionada}
      <div class="rounded-xl border border-zinc-200 p-4 shadow-sm bg-white">
        <h3 class="text-base font-semibold mb-2">{seleccionada.nombre}</h3>
        <dl class="space-y-1 text-sm">
          <div class="flex justify-between gap-4">
            <dt class="text-zinc-500">Estado</dt>
            <dd class="font-medium" style="color: {COLORES_ESTADO[seleccionada.estado].bg}">{COLORES_ESTADO[seleccionada.estado].label}</dd>
          </div>
          <div class="flex justify-between gap-4">
            <dt class="text-zinc-500">Fecha</dt><dd>{seleccionada.fecha} {seleccionada.hora}</dd>
          </div>
          <div class="flex justify-between gap-4">
            <dt class="text-zinc-500">Personas</dt><dd>{seleccionada.personas}</dd>
          </div>
          <div class="flex justify-between gap-4">
            <dt class="text-zinc-500">Piso</dt><dd>{seleccionada.planta ?? "sin preferencia"}</dd>
          </div>
          <div class="flex justify-between gap-4">
            <dt class="text-zinc-500">Teléfono</dt><dd class="font-mono text-xs">{seleccionada.telefono}</dd>
          </div>
        </dl>
        <button
          class="mt-3 text-sm rounded-lg px-3 py-1.5 border border-zinc-300 hover:bg-zinc-50 active:bg-zinc-100"
          onclick={() => (seleccionada = null)}
        >Cerrar</button>
      </div>
    {/if}

    <section class="rounded-xl border border-zinc-200 p-4 bg-white">
      <h2 class="text-sm font-semibold uppercase tracking-wide text-zinc-500 mb-2">Eventos capturados</h2>
      {#if log.length === 0}
        <p class="text-sm text-zinc-400 italic">Interactuá con el calendario.</p>
      {:else}
        <ul class="text-sm space-y-1 max-h-72 overflow-auto">
          {#each log as e}
            <li class="flex gap-2">
              <time class="text-xs text-zinc-400 tabular-nums pt-0.5">{e.t}</time>
              <span>{e.msg}</span>
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  </aside>
</main>
