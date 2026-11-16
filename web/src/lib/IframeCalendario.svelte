<script lang="ts">
  /**
   * Vista semanal de reservas para el iframe de Chatwoot (spike del pedido
   * 10 de Valen: "espejo de la vista de calendario del dashboard con vista
   * semanal y drag, ya con los datos del contacto").
   *
   * A diferencia del dashboard, acá no hay sesión: los endpoints se
   * autorizan con la clave de embed (key en la URL) y el contacto llega
   * por el evento appContext que Chatwoot publica en el window. Es el
   * mismo mecanismo que usa el iframe de formularios (/iframe/app).
   */
  import { onDestroy, onMount } from "svelte";
  import dayGridPlugin from "fullcalendar/daygrid";
  import timeGridPlugin from "fullcalendar/timegrid";
  import interactionPlugin from "fullcalendar/interaction";
  import formaThemePlugin from "fullcalendar/themes/forma";
  import esLocale from "fullcalendar/locales/es";
  import type { Calendar, CalendarOptions } from "fullcalendar";
  import FullCalendarHost from "./FullCalendarHost.svelte";
  import { COLORES_ESTADO, type Reserva } from "./reservas";
  import "fullcalendar/skeleton.css";
  import "fullcalendar/themes/forma/theme.css";
  import "fullcalendar/themes/forma/palettes/blue.css";

  const POLL_MS = 20_000;

  let key = "";
  let contactoNombre = $state("");
  let telefono = $state("");
  let estadoFuente = $state("esperando contexto de Chatwoot…");
  let avisos = $state<string[]>([]);
  let seleccionada = $state<Reserva | null>(null);
  let reservas = $state<Reserva[]>([]);
  let cal = $state<Calendar>();
  let pollTimer: ReturnType<typeof setInterval> | undefined;

  function avisar(msg: string) {
    avisos = [msg, ...avisos].slice(0, 5);
  }

  function fechaISO(d: Date): string {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function horaISO(d: Date): string {
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }

  /** La API del dashboard devuelve "HH:MM:SS"; el iframe, "HH:MM". */
  function hhmm(h: string): string {
    return h.slice(0, 5);
  }

  async function cargar() {
    if (!key || !telefono) return;
    try {
      const res = await fetch(`/iframe/reservas.json?key=${encodeURIComponent(key)}&tel=${encodeURIComponent(telefono)}`);
      if (res.ok) {
        reservas = await res.json();
        estadoFuente = `${reservas.length} reserva(s) del contacto`;
      } else {
        estadoFuente = `error ${res.status} al cargar`;
      }
    } catch {
      estadoFuente = "sin conexión con la API";
    }
  }

  async function crear(fecha: string, hora: string) {
    const ok = window.confirm(
      `Crear reserva de ${contactoNombre || telefono} el ${fecha} a las ${hora}?`,
    );
    if (!ok) return;
    const res = await fetch(`/iframe/reservas.json?key=${encodeURIComponent(key)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        nombre: contactoNombre || telefono,
        telefono,
        fecha,
        hora,
        personas: 2,
      }),
    });
    avisar(res.status === 201 ? `reserva creada ${fecha} ${hora}` : `error al crear (${res.status})`);
    await cargar();
  }

  async function reprogramar(id: number, fecha: string, hora: string) {
    const res = await fetch(`/iframe/reservas/${id}.json?key=${encodeURIComponent(key)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fecha, hora }),
    });
    avisar(res.ok ? `reserva #${id} → ${fecha} ${hora}` : `error al guardar #${id} (${res.status})`);
    await cargar();
  }

  /** appContext: Chatwoot avisa el contacto de la conversación abierta. */
  function onMessage(ev: MessageEvent) {
    let data: any;
    try {
      data = typeof ev.data === "string" ? JSON.parse(ev.data) : ev.data;
    } catch {
      return; // mensaje que no es para nosotros
    }
    if (!data || data.event !== "appContext" || !data.data) return;
    const contact = data.data.contact ?? {};
    const tel = contact.phone_number || contact.phone || "";
    if (!tel) {
      estadoFuente = "el contacto no tiene teléfono";
      return;
    }
    contactoNombre = contact.name || "";
    telefono = tel;
    cargar();
  }

  const options = $derived.by<CalendarOptions>(() => ({
    plugins: [timeGridPlugin, dayGridPlugin, interactionPlugin, formaThemePlugin],
    locales: [esLocale],
    locale: "es",
    initialView: "timeGridWeek",
    headerToolbar: { left: "prev,next today", center: "title", right: "timeGridWeek,timeGridDay,dayGridMonth" },
    buttonText: { today: "Hoy", week: "Semana", day: "Día", month: "Mes" },
    // Sidebar de Chatwoot: angosta. Sin slot de all-day, media hora por fila,
    // altura fija con scroll interno, abre a la tarde (hora pico del bar).
    allDaySlot: false,
    slotDuration: "00:30:00",
    slotLabelInterval: "01:00",
    height: "560px",
    scrollTime: "17:00",
    expandRows: true,

    editable: true,
    eventResizableFromStart: false,
    selectable: true,
    nowIndicator: true,
    navLinks: true,
    dayMaxEvents: true,

    events: $state.snapshot(events) as any,

    dateClick(info) {
      crear(fechaISO(info.date), horaISO(info.date));
    },
    select(info) {
      crear(fechaISO(info.start), horaISO(info.start));
    },
    eventClick(info) {
      seleccionada = (info.event.extendedProps as { reserva: Reserva }).reserva;
    },
    eventDrop(info) {
      const r = (info.event.extendedProps as { reserva: Reserva }).reserva;
      const d = info.event.start!;
      reprogramar(r.id, fechaISO(d), horaISO(d));
    },
  }));

  const events = $derived.by(() =>
    reservas.map((r) => ({
      id: String(r.id),
      title: `${r.nombre} · ${r.personas}p`,
      start: `${r.fecha}T${hhmm(r.hora)}:00`,
      color: COLORES_ESTADO[r.estado]?.bg ?? COLORES_ESTADO.pendiente.bg,
      backgroundColor: COLORES_ESTADO[r.estado]?.bg ?? COLORES_ESTADO.pendiente.bg,
      borderColor: COLORES_ESTADO[r.estado]?.border ?? COLORES_ESTADO.pendiente.border,
      extendedProps: { reserva: r },
    })),
  );

  // Sincroniza eventos sin recrear el calendario.
  $effect(() => {
    if (cal) cal.setOption("events", $state.snapshot(events) as any);
  });

  onMount(() => {
    key = new URLSearchParams(location.search).get("key") ?? "";
    window.addEventListener("message", onMessage);
    pollTimer = setInterval(cargar, POLL_MS);
  });

  onDestroy(() => {
    window.removeEventListener("message", onMessage);
    clearInterval(pollTimer);
  });
</script>

<header class="flex items-baseline gap-2 px-3 py-2 border-b border-zinc-200">
  <span class="font-semibold">Losco</span>
  <span class="text-xs text-zinc-500 truncate">{contactoNombre || telefono || "sin contacto"}</span>
  <span class="ml-auto text-[10px] rounded-full px-2 py-0.5 bg-blue-50 text-blue-700 whitespace-nowrap">{estadoFuente}</span>
</header>

{#if avisos.length}
  <div class="px-3 py-1 text-xs text-zinc-600 bg-zinc-50 border-b border-zinc-100">
    {#each avisos as a}
      <div>{a}</div>
    {/each}
  </div>
{/if}

{#if seleccionada}
  <div class="mx-2 mt-2 rounded-lg border border-zinc-200 p-3 shadow-sm bg-white">
    <div class="flex items-baseline justify-between gap-2">
      <h3 class="text-sm font-semibold">{seleccionada.nombre}</h3>
      <button
        class="text-xs rounded px-2 py-0.5 border border-zinc-300 hover:bg-zinc-50"
        onclick={() => (seleccionada = null)}
      >Cerrar</button>
    </div>
    <dl class="mt-1.5 text-xs space-y-0.5">
      <div class="flex justify-between gap-3">
        <dt class="text-zinc-500">Estado</dt>
        <dd class="font-medium" style="color: {COLORES_ESTADO[seleccionada.estado]?.bg}">{seleccionada.estado}</dd>
      </div>
      <div class="flex justify-between gap-3">
        <dt class="text-zinc-500">Fecha</dt><dd>{seleccionada.fecha} {seleccionada.hora}</dd>
      </div>
      <div class="flex justify-between gap-3">
        <dt class="text-zinc-500">Personas</dt><dd>{seleccionada.personas}</dd>
      </div>
      {#if seleccionada.planta}
        <div class="flex justify-between gap-3">
          <dt class="text-zinc-500">Piso</dt><dd>{seleccionada.planta}</dd>
        </div>
      {/if}
      <div class="flex justify-between gap-3">
        <dt class="text-zinc-500">Teléfono</dt><dd class="font-mono">{seleccionada.telefono}</dd>
      </div>
      {#if seleccionada.detalles}
        <div class="flex justify-between gap-3">
          <dt class="text-zinc-500">Detalle</dt><dd class="text-right">{seleccionada.detalles}</dd>
        </div>
      {/if}
    </dl>
  </div>
{/if}

<div class="p-2">
  <FullCalendarHost {options} bind:cal />
</div>
