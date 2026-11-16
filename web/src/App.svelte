<script lang="ts">
  import { onMount } from "svelte";
  import CalendarioReservas from "./lib/CalendarioReservas.svelte";
  import { COLORES_ESTADO, type Reserva } from "./lib/reservas";
  // Look vanilla de FullCalendar (como los demos): theme forma + paleta blue.
  // Estos imports van DESPUÉS de app.css (ver app.css: cascade a propósito).
  import "fullcalendar/skeleton.css";
  import "fullcalendar/themes/forma/theme.css";
  import "fullcalendar/themes/forma/palettes/blue.css";

  /** Datos demo para el spike (fechas alrededor de hoy). */
  const MOCK: Reserva[] = [
    { id: 1, nombre: "Mesa Giacobbe", telefono: "5491155551010", fecha: "2026-11-20", hora: "21:00", personas: 12, estado: "confirmada", planta: "baja" },
    { id: 2, nombre: "Cumple de Ana", telefono: "5491155551011", fecha: "2026-11-20", hora: "22:30", personas: 40, estado: "agendada", planta: "alta" },
    { id: 3, nombre: "Los de Palermo", telefono: "5491155551012", fecha: "2026-11-20", hora: "23:00", personas: 35, estado: "pendiente", planta: null },
    { id: 4, nombre: "Cena empresa XYZ", telefono: "5491155551013", fecha: "2026-11-21", hora: "20:30", personas: 60, estado: "confirmada", planta: "alta" },
    { id: 5, nombre: "Rodríguez", telefono: "5491155551014", fecha: "2026-11-21", hora: "22:00", personas: 10, estado: "agendada", planta: "baja" },
    { id: 6, nombre: "Aniversario Juana", telefono: "5491155551015", fecha: "2026-11-22", hora: "21:00", personas: 90, estado: "confirmada", planta: "baja" },
    { id: 7, nombre: "Mesa 4 amigos", telefono: "5491155551016", fecha: "2026-11-22", hora: "22:15", personas: 18, estado: "agendada", planta: null },
    { id: 8, nombre: "Despedida Nico", telefono: "5491155551017", fecha: "2026-11-27", hora: "21:30", personas: 55, estado: "agendada", planta: "alta" },
  ];

  let reservas = $state<Reserva[]>(MOCK);
  let fuente = $state("demo");
  let log = $state<{ t: string; msg: string }[]>([]);
  let seleccionada = $state<Reserva | null>(null);

  function anotar(msg: string) {
    const t = new Date().toLocaleTimeString("es-AR");
    log = [{ t, msg }, ...log].slice(0, 40);
  }

  onMount(async () => {
    // Si uvicorn está corriendo con sesión activa, usa las reservas reales.
    try {
      const res = await fetch("/api/reservas");
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          reservas = data;
          fuente = `API real (${data.length} reservas)`;
        }
      }
    } catch {
      /* sin backend → queda el demo */
    }
  });
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
    onDateClick={(f) => anotar(`alta de reserva para ${f} (abriría el form precargado)`)}
    onDateSelect={(ini, fin) => anotar(`selección ${ini} → ${fin} (abriría el form con ese rango)`)}
    onEventClick={(r) => (seleccionada = r)}
    onEventDrop={(r, f, h) => anotar(`reserva #${r.id} "${r.nombre}" → ${f} ${h} (PATCH)`)}
    onEventResize={(r, h) => anotar(`reserva #${r.id} "${r.nombre}" ahora termina ${h} (PATCH)`)}
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
