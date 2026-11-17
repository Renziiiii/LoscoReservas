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
  import { fade } from "svelte/transition";
  import dayGridPlugin from "fullcalendar/daygrid";
  import timeGridPlugin from "fullcalendar/timegrid";
  import interactionPlugin from "fullcalendar/interaction";
  import formaThemePlugin from "fullcalendar/themes/forma";
  import esLocale from "fullcalendar/locales/es";
  import type { Calendar, CalendarOptions } from "fullcalendar";
  import FullCalendarHost from "./FullCalendarHost.svelte";
  import { COLORES_ESTADO, ESTADOS, type Estado, type Reserva } from "./reservas";
  import "fullcalendar/skeleton.css";
  import "fullcalendar/themes/forma/theme.css";
  import "fullcalendar/themes/forma/palettes/blue.css";

  const POLL_MS = 20_000;

  let key = "";
  let contactoNombre = $state("");
  let telefono = $state("");
  let estadoFuente = $state("esperando contexto de Chatwoot…");
  /** Aviso único y efímero (no un log): el resultado de la última acción. */
  let toast = $state<{ texto: string; tipo: "ok" | "err" } | null>(null);
  let toastTimer: ReturnType<typeof setTimeout> | undefined;
  function avisar(texto: string, tipo: "ok" | "err" = "ok") {
    toast = { texto, tipo };
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast = null), 4000);
  }
  let seleccionada = $state<Reserva | null>(null);
  /** "editar" con la reserva clickeada, "crear" con fecha/hora precargadas. */
  let modo = $state<"editar" | "crear" | null>(null);
  /** Borrador editable del panel: sólo viaja al backend al guardar. */
  let draft = $state({
    nombre: "",
    fecha: "",
    hora: "",
    personas: 2,
    estado: "pendiente" as Estado,
    detalles: "",
    enviar_recordatorio: true,
  } | null);
  let guardando = $state(false);
  /** Baja en dos clicks SIN window.confirm: los iframes de Chatwoot pueden
   * bloquear los diálogos nativos (sandbox sin allow-modals los hace
   * devolver false en silencio — por eso "el botón no hace nada"). */
  let confirmandoEliminar = $state(false);
  let confirmarTimer: ReturnType<typeof setTimeout> | undefined;

  function cargarDraft(r: Reserva) {
    draft = {
      nombre: r.nombre,
      fecha: r.fecha,
      hora: hhmm(r.hora),
      personas: r.personas,
      estado: r.estado,
      detalles: r.detalles ?? "",
      enviar_recordatorio: r.enviar_recordatorio ?? true,
    };
  }

  function abrir(r: Reserva) {
    seleccionada = r;
    modo = "editar";
    cargarDraft(r);
  }

  /** Alta desde el calendario (dateClick/select): mismo panel, modo crear. */
  function abrirCrear(fecha: string, hora: string) {
    if (!telefono) {
      avisar("el contacto no tiene teléfono cargado", "err");
      return;
    }
    seleccionada = null;
    modo = "crear";
    draft = {
      nombre: contactoNombre || telefono,
      fecha,
      hora,
      personas: 2,
      estado: "pendiente",
      detalles: "",
      enviar_recordatorio: true,
    };
    confirmandoEliminar = false;
  }

  function cerrar() {
    seleccionada = null;
    modo = null;
    draft = null;
    confirmandoEliminar = false;
  }
  let reservas = $state<Reserva[]>([]);
  let cal = $state<Calendar>();
  let pollTimer: ReturnType<typeof setInterval> | undefined;

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

  async function reprogramar(id: number, fecha: string, hora: string) {
    const res = await fetch(`/iframe/reservas/${id}.json?key=${encodeURIComponent(key)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fecha, hora }),
    });
    if (res.ok) {
      await cargar();
      // Si la reserva editada está abierta en el panel, refrescala con lo nuevo.
      if (seleccionada?.id === id) {
        const fresca = reservas.find((r) => r.id === id);
        if (fresca) {
          seleccionada = fresca;
          cargarDraft(fresca);
        }
      }
    } else {
      avisar(`no se pudo mover la reserva #${id} (${res.status})`, "err");
      await cargar(); // vuelve el evento a su lugar
    }
  }

  /** POST (modo crear) o PATCH (modo editar) del borrador — endpoints T9/T10. */
  async function guardar() {
    if (!draft || guardando) return;
    guardando = true;
    try {
      const cuerpo = JSON.stringify({
        nombre: draft.nombre,
        fecha: draft.fecha,
        hora: draft.hora,
        personas: draft.personas,
        estado: draft.estado,
        detalles: draft.detalles || null,
        enviar_recordatorio: draft.enviar_recordatorio,
      });
      const url = modo === "crear"
        ? `/iframe/reservas.json?key=${encodeURIComponent(key)}`
        : `/iframe/reservas/${seleccionada?.id}.json?key=${encodeURIComponent(key)}`;
      const res = await fetch(url, {
        method: modo === "crear" ? "POST" : "PATCH",
        headers: { "Content-Type": "application/json" },
        body: cuerpo,
      });
      if (res.ok) {
        avisar(modo === "crear" ? "reserva creada" : "reserva guardada");
        cerrar();
        await cargar();
      } else {
        const data = await res.json().catch(() => null);
        const msjs = (data?.detail ?? [])
          .map((e: { msg?: string }) => e.msg ?? "")
          .filter(Boolean);
        avisar(msjs.length ? msjs.join(" · ") : `error al guardar (${res.status})`, "err");
      }
    } catch {
      avisar("sin conexión con la API", "err");
    } finally {
      guardando = false;
    }
  }

  /** DELETE en dos clicks dentro del panel (endpoint T10). */
  async function eliminar() {
    if (!seleccionada || guardando) return;
    if (!confirmandoEliminar) {
      confirmandoEliminar = true;
      clearTimeout(confirmarTimer);
      confirmarTimer = setTimeout(() => (confirmandoEliminar = false), 4000);
      return;
    }
    guardando = true;
    const id = seleccionada.id;
    try {
      const res = await fetch(`/iframe/reservas/${id}.json?key=${encodeURIComponent(key)}`, { method: "DELETE" });
      if (res.status === 204) {
        avisar(`reserva #${id} eliminada`);
        cerrar();
        await cargar();
      } else {
        avisar(`no se pudo eliminar la reserva #${id} (${res.status})`, "err");
      }
    } catch {
      avisar("sin conexión con la API", "err");
    } finally {
      guardando = false;
    }
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
      abrirCrear(fechaISO(info.date), horaISO(info.date));
    },
    select(info) {
      abrirCrear(fechaISO(info.start), horaISO(info.start));
    },
    eventClick(info) {
      abrir((info.event.extendedProps as { reserva: Reserva }).reserva);
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
    clearTimeout(toastTimer);
    clearTimeout(confirmarTimer);
  });
</script>

<header class="flex items-baseline gap-2 px-3 py-2 border-b border-zinc-200">
  <span class="font-semibold">Losco</span>
  <span class="text-xs text-zinc-500 truncate">{contactoNombre || telefono || "sin contacto"}</span>
  <span class="ml-auto text-[10px] rounded-full px-2 py-0.5 bg-blue-50 text-blue-700 whitespace-nowrap">{estadoFuente}</span>
</header>

{#if toast}
  <div
    class="px-3 py-1.5 text-xs border-b {toast.tipo === 'ok' ? 'text-teal-800 bg-teal-50' : 'text-red-800 bg-red-50'}"
    role="status"
    transition:fade
  >{toast.texto}</div>
{/if}

{#if draft}
  <div class="mx-2 mt-2 rounded-lg border border-zinc-200 p-3 shadow-sm bg-white">
    <div class="flex items-baseline justify-between gap-2">
      <h3 class="text-sm font-semibold">
        {#if modo === "crear"}Nueva reserva{:else}Reserva Nº {seleccionada?.id}{/if}
        {#if modo === "editar"} · {seleccionada?.telefono}{/if}
      </h3>
      <button
        type="button"
        class="text-xs rounded px-2 py-0.5 border border-zinc-300 hover:bg-zinc-50"
        onclick={cerrar}
      >Cerrar</button>
    </div>
    <form
      class="mt-2 grid grid-cols-2 gap-2 text-xs"
      onsubmit={(e) => { e.preventDefault(); guardar(); }}
    >
      <label class="col-span-2 flex flex-col gap-0.5">
        <span class="text-zinc-500">Nombre</span>
        <input
          class="rounded border border-zinc-300 px-2 py-1"
          type="text"
          bind:value={draft.nombre}
          required
        />
      </label>
      <label class="flex flex-col gap-0.5">
        <span class="text-zinc-500">Fecha</span>
        <input class="rounded border border-zinc-300 px-2 py-1" type="date" bind:value={draft.fecha} required />
      </label>
      <label class="flex flex-col gap-0.5">
        <span class="text-zinc-500">Hora</span>
        <input class="rounded border border-zinc-300 px-2 py-1" type="time" bind:value={draft.hora} required />
      </label>
      <label class="flex flex-col gap-0.5">
        <span class="text-zinc-500">Personas</span>
        <input
          class="rounded border border-zinc-300 px-2 py-1"
          type="number"
          min="1"
          max="200"
          bind:value={draft.personas}
          required
        />
      </label>
      <label class="flex flex-col gap-0.5">
        <span class="text-zinc-500">Estado</span>
        <select class="rounded border border-zinc-300 px-2 py-1" bind:value={draft.estado}>
          {#each ESTADOS as e}
            <option value={e}>{COLORES_ESTADO[e].label}</option>
          {/each}
        </select>
      </label>
      <label class="col-span-2 flex flex-col gap-0.5">
        <span class="text-zinc-500">Detalles</span>
        <input
          class="rounded border border-zinc-300 px-2 py-1"
          type="text"
          bind:value={draft.detalles}
          placeholder="ej. cumpleaños, mesa cerca de la ventana…"
        />
      </label>
      <label class="col-span-2 flex items-center gap-2">
        <input type="checkbox" bind:checked={draft.enviar_recordatorio} />
        <span class="text-zinc-500">Enviar recordatorio por WhatsApp</span>
      </label>
      <div class="col-span-2 flex gap-2">
        <button
          type="submit"
          class="flex-1 rounded bg-blue-600 px-2 py-1.5 font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          disabled={guardando}
        >{guardando ? "Guardando…" : modo === "crear" ? "Crear reserva" : "Guardar"}</button>
        {#if modo === "editar"}
          <button
            type="button"
            class="rounded border px-2 py-1.5 font-medium disabled:opacity-50 {confirmandoEliminar ? 'border-red-600 bg-red-600 text-white' : 'border-red-200 bg-red-50 text-red-700 hover:bg-red-100'}"
            onclick={eliminar}
            disabled={guardando}
          >{confirmandoEliminar ? "¿Seguro? Click de nuevo" : "Eliminar"}</button>
        {/if}
      </div>
    </form>
  </div>
{/if}

<div class="p-2">
  <FullCalendarHost {options} bind:cal />
</div>
