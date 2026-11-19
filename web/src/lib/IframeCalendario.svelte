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
  import InputTelefono from "./InputTelefono.svelte";
  import {
    atenuarColor,
    coloresEstado,
    cargarEstados,
    ESTADOS,
    nombreDeEstado,
    nombresDeEstados,
    type Estado,
    type Reserva,
  } from "./reservas";
  import "fullcalendar/skeleton.css";
  import "fullcalendar/themes/forma/theme.css";
  import "fullcalendar/themes/forma/palettes/blue.css";

  const POLL_MS = 20_000;

  let key = "";
  let contactoNombre = $state("");
  let contactoId = $state<number | null>(null);
  /** S11: editable — the contact's phone, or the number the agent
   * annotates when the Chatwoot contact has none (Instagram chats). */
  let telefono = $state("");
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
  /** Estados configurados (T5): el fallback base hasta que la llamada a
   * /iframe/estados.json llegue.Se referencia en la derivación de
   * eventos para recomputar los colores cuando llega. */
  let listaEstados = $state<Estado[]>(ESTADOS);
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
    // S11: el teléfono es editable también al editar — corregirlo
    // sincroniza el contacto de Chatwoot (sólo si está vacío).
    telefono = r.telefono;
  }

  function abrir(r: Reserva) {
    seleccionada = r;
    modo = "editar";
    cargarDraft(r);
  }

  /** Alta desde el calendario (dateClick/select): mismo panel, modo crear.
   * S11: sin guardia de teléfono — el campo es editable en el formulario. */
  function abrirCrear(fecha: string, hora: string) {
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

  /** L61: matchea por dígitos el contacto de Chatwoot (mismo criterio del
   * backend _por_telefono) para marcar sus reservas en el calendario. */
  function esDelContacto(r: Reserva): boolean {
    return r.telefono.replace(/\D/g, "") === telefono.replace(/\D/g, "");
  }

  async function cargar() {
    if (!key || !telefono) return;
    try {
      // L61: el calendario muestra TODAS las reservas (disponibilidad del
      // bar); las del contacto se marcan visualmente (losco-propia).
      const res = await fetch(
        `/iframe/reservas.json?key=${encodeURIComponent(key)}&tel=${encodeURIComponent(telefono)}&todas=1`,
      );
      if (res.ok) {
        reservas = await res.json();
      } else {
        avisar(`error ${res.status} al cargar las reservas`, "err");
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
        // POST exige telefono (ReservaCreate); PATCH lo acepta para
        // corregirlo. S11: es el del contacto o el que el agente anotó;
        // contacto_chatwoot_id pide al backend escribir el número en el
        // contacto de Chatwoot sólo si está vacío (fail-soft,
        // app/contactos.py). JSON.stringify descarta claves undefined.
        telefono,
        contacto_chatwoot_id: contactoId ?? undefined,
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
          .map((e: { loc?: (string | number)[]; msg?: string }) => {
            // "body.telefono" + "Field required" → "telefono: Field required"
            const campo = e.loc?.filter((p) => p !== "body").join(".") ?? "";
            return campo ? `${campo}: ${e.msg ?? ""}` : (e.msg ?? "");
          })
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

  /** appContext: Chatwoot avisa el contacto de la conversación abierta. */  function onMessage(ev: MessageEvent) {
    let data: any;
    try {
      data = typeof ev.data === "string" ? JSON.parse(ev.data) : ev.data;
    } catch {
      return; // mensaje que no es para nosotros
    }
    if (!data || data.event !== "appContext" || !data.data) return;
    const contact = data.data.contact ?? {};
    contactoId = typeof contact.id === "number" ? contact.id : null;
    contactoNombre = contact.name || "";
    // S11: a contact without a phone (Instagram chats) blocks nothing —
    // the agent annotates the number in the create form and the
    // reservations list loads once the phone is known.
    telefono = contact.phone_number || contact.phone || "";
    cargar();
  }

  /** L66: render custom del evento — personas con 👤 adelante del nombre
   * y la descripción visible en preview (2ª línea truncada), sin entrar al
   * evento. HTML escapado a mano: nombre/detalles son texto de clientes. */
  function esc(s: string): string {
    return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
  }

  function eventContent(info: any) {
    const r = (info.event.extendedProps as { reserva: Reserva }).reserva;
    const mes = info.view.type === "dayGridMonth";
    const cabecera = mes
      ? `${r.personas} 👤 ${esc(r.nombre)}`
      : `${hhmm(r.hora)} · ${r.personas} 👤 ${esc(r.nombre)}`;
    const detalle = r.detalles
      ? `<div class="truncate text-[10px] font-normal opacity-80">${esc(r.detalles)}</div>`
      : "";
    return {
      html: `<div class="overflow-hidden px-1 leading-tight"><div class="truncate text-[11px] font-semibold">${cabecera}</div>${detalle}</div>`,
    };
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
    // L65: sin header ni padding — el calendario aprovecha todo el alto
    // del iframe de Chatwoot (el shell fija html/body/#app a 100%).
    height: "100%",
    scrollTime: "17:00",
    expandRows: true,

    editable: true,
    eventResizableFromStart: false,
    selectable: true,
    nowIndicator: true,
    navLinks: true,
    dayMaxEvents: true,

    events: $state.snapshot(events) as any,

    eventContent,
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

  const events = $derived.by(() => {
    void listaEstados; // T5: recomputa los colores cuando llegan los estados
    return reservas.map((r) => {
      const c = coloresEstado(r.estado);
      // L64: los eventos de OTROS contactos llevan el color mezclado hacia
      // gris (inline, no depende de clases); los del contacto llevan el
      // color pleno + ring azul. El borde de 2px lo fija una clase utility
      // porque FC pinta border-color por estilo inline.
      const propia = esDelContacto(r);
      return {
        id: String(r.id),
        title: `${r.nombre} · ${r.personas}p`,
        start: `${r.fecha}T${hhmm(r.hora)}:00`,
        color: propia ? c.bg : atenuarColor(c.bg),
        backgroundColor: propia ? c.bg : atenuarColor(c.bg),
        borderColor: propia ? c.border : atenuarColor(c.border, 0.75),
        classNames: propia ? ["ring-2 ring-brand shadow-md"] : [],
        extendedProps: { reserva: r },
      };
    });
  });

  // Sincroniza eventos sin recrear el calendario.
  $effect(() => {
    if (cal) cal.setOption("events", $state.snapshot(events) as any);
  });

  onMount(async () => {
    key = new URLSearchParams(location.search).get("key") ?? "";
    window.addEventListener("message", onMessage);
    // T5: la lista configurable alimenta el select del panel y los
    // colores; el fallback base cubre el fallo del fetch.
    if (await cargarEstados(`/iframe/estados.json?key=${encodeURIComponent(key)}`)) {
      listaEstados = nombresDeEstados();
    }
    pollTimer = setInterval(cargar, POLL_MS);
  });

  onDestroy(() => {
    window.removeEventListener("message", onMessage);
    clearInterval(pollTimer);
    clearTimeout(toastTimer);
    clearTimeout(confirmarTimer);
  });
</script>

<div class="flex h-full flex-col overflow-hidden">
{#if toast}
  <div
    class="px-3 py-1.5 text-xs {toast.tipo === 'ok' ? 'text-ok-text bg-ok-surface' : 'text-danger-text bg-danger-surface'}"
    role="status"
    transition:fade
  >{toast.texto}</div>
{/if}

{#if draft}
  <!-- L15: flush contra el borde (L65), el usuario pidió un margen —
  separa el panel del calendario; los inputs llevan su propio padding. -->
  <div class="border border-line rounded bg-surface my-2 mx-2">
    <div class="flex items-baseline justify-between gap-2">
      <h3 class="text-sm font-semibold">
        {#if modo === "crear"}Nueva reserva{:else}Reserva Nº {seleccionada?.id}{/if}
        {#if modo === "editar"} · {seleccionada?.telefono}{/if}
      </h3>
      <button
        type="button"
        class="text-xs rounded px-2 py-0.5 border border-line-strong hover:bg-surface-hover text-muted"
        onclick={cerrar}
      >Cerrar</button>
    </div>
    <form
      class="mt-2 grid grid-cols-2 gap-2 text-xs"
      onsubmit={(e) => { e.preventDefault(); guardar(); }}
    >
      <label class="col-span-2 flex flex-col gap-0.5">
        <span class="text-muted">Nombre</span>
        <input
          class="rounded border border-line-strong bg-surface text-fg px-2 py-1"
          type="text"
          bind:value={draft.nombre}
          required
        />
      </label>
      <label class="col-span-2 flex flex-col gap-0.5">
        <span class="text-muted">Teléfono</span>
        <InputTelefono bind:value={telefono} />
        {#if !telefono}
          <span class="text-muted">
            El contacto no tiene teléfono en Chatwoot — elegí el país y anotá
            el número (ej: 9 321 098 7654); queda registrado también en su
            contacto.
          </span>
        {/if}
      </label>
      <label class="flex flex-col gap-0.5">
        <span class="text-muted">Fecha</span>
        <input class="rounded border border-line-strong bg-surface text-fg px-2 py-1" type="date" bind:value={draft.fecha} required />
      </label>
      <label class="flex flex-col gap-0.5">
        <span class="text-muted">Hora</span>
        <input class="rounded border border-line-strong bg-surface text-fg px-2 py-1" type="time" bind:value={draft.hora} required />
      </label>
      <label class="flex flex-col gap-0.5">
        <span class="text-muted">Personas</span>
        <input
          class="rounded border border-line-strong bg-surface text-fg px-2 py-1"
          type="number"
          min="1"
          max="200"
          bind:value={draft.personas}
          required
        />
      </label>
      <label class="flex flex-col gap-0.5">
        <span class="text-muted">Estado</span>
        <select class="rounded border border-line-strong bg-surface text-fg px-2 py-1" bind:value={draft.estado}>
          {#each listaEstados as e}
            <option value={e}>{nombreDeEstado(e)}</option>
          {/each}
        </select>
      </label>
      <label class="col-span-2 flex flex-col gap-0.5">
        <span class="text-muted">Detalles</span>
        <input
          class="rounded border border-line-strong bg-surface text-fg px-2 py-1"
          type="text"
          bind:value={draft.detalles}
          placeholder="ej. cumpleaños, mesa cerca de la ventana…"
        />
      </label>
      <label class="col-span-2 flex items-center gap-2">
        <input type="checkbox" bind:checked={draft.enviar_recordatorio} />
        <span class="text-muted">Enviar recordatorio por WhatsApp</span>
      </label>
      <div class="col-span-2 flex gap-2">
        <button
          type="submit"
          class="flex-1 rounded bg-brand px-2 py-1.5 font-medium text-on-brand hover:bg-brand-hover disabled:opacity-50"
          disabled={guardando}
        >{guardando ? "Guardando…" : modo === "crear" ? "Crear reserva" : "Guardar"}</button>
        {#if modo === "editar"}
          <button
            type="button"
            class="rounded border px-2 py-1.5 font-medium disabled:opacity-50 {confirmandoEliminar ? 'border-danger bg-danger text-on-brand' : 'border-danger-soft bg-danger-surface text-danger-text hover:bg-danger-soft'}"
            onclick={eliminar}
            disabled={guardando}
          >{confirmandoEliminar ? "¿Seguro? Click de nuevo" : "Eliminar"}</button>
        {/if}
      </div>
    </form>
  </div>
{/if}

<div class="min-h-0 flex-1">
  <FullCalendarHost {options} bind:cal />
</div>
</div>
