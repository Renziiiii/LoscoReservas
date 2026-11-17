<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import CalendarioReservas from "./lib/CalendarioReservas.svelte";
  import { COLORES_ESTADO, ESTADOS, type Estado, type Reserva } from "./lib/reservas";
  // Look vanilla de FullCalendar (como los demos): theme forma + paleta blue.
  // Estos imports van DESPUÉS de app.css (ver app.css: cascade a propósito).
  import "fullcalendar/skeleton.css";
  import "fullcalendar/themes/forma/theme.css";
  import "fullcalendar/themes/forma/palettes/blue.css";

  let reservas = $state<Reserva[]>([]);
  let fuente = $state("cargando…");
  let seleccionada = $state<Reserva | null>(null);
  /** "editar" con la reserva clickeada, "crear" con fecha/hora precargadas. */
  let modo = $state<"editar" | "crear" | null>(null);
  /** Borrador editable del panel: sólo viaja al backend al guardar (S10). */
  let draft = $state({
    nombre: "",
    telefono: "",
    fecha: "",
    hora: "",
    personas: 2,
    estado: "pendiente" as Estado,
    detalles: "",
    enviar_recordatorio: true,
  } | null);
  let guardando = $state(false);
  /** Baja en dos clicks, sin window.confirm (mismo criterio del iframe). */
  let confirmandoEliminar = $state(false);
  let confirmarTimer: ReturnType<typeof setTimeout> | undefined;

  /** Aviso único y efímero, como en el iframe. */
  let toast = $state<{ texto: string; tipo: "ok" | "err" } | null>(null);
  let toastTimer: ReturnType<typeof setTimeout> | undefined;
  function avisar(texto: string, tipo: "ok" | "err" = "ok") {
    toast = { texto, tipo };
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast = null), 4000);
  }

  // --- S8: settings de mensajes automatizados dentro del SPA ---
  let ajustesAbierto = $state(false);
  let ajustes = $state({
    hora_confirmacion: "",
    hora_invitados: "",
    plantilla_confirmacion: "",
    plantilla_invitados: "",
  } | null);
  let guardandoAjustes = $state(false);
  let plantillas = $state<{ name: string; variables: string[] }[]>([]);
  let plantillasMotivo = $state<string | null>(null);

  async function abrirAjustes() {
    ajustesAbierto = true;
    ajustes = null;
    try {
      const [resConf, resPla] = await Promise.all([fetch("/api/ajustes"), fetch("/ajustes/plantillas.json")]);
      if (resConf.ok) ajustes = await resConf.json();
      else avisar(`no se pudo cargar la configuración (${resConf.status})`, "err");
      if (resPla.ok) {
        const body = await resPla.json();
        plantillas = body.plantillas ?? [];
        plantillasMotivo = body.motivo ?? null;
      }
    } catch {
      avisar("sin conexión con la API", "err");
    }
  }

  async function guardarAjustes() {
    if (!ajustes || guardandoAjustes) return;
    guardandoAjustes = true;
    try {
      const res = await fetch("/api/ajustes", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(ajustes),
      });
      if (res.ok) {
        avisar("configuración de mensajes guardada");
      } else {
        const data = await res.json().catch(() => null);
        const detail = data?.detail ?? {};
        const msjs =
          typeof detail === "object" && !Array.isArray(detail)
            ? Object.entries(detail).map(([campo, msg]) => `${campo}: ${msg}`)
            : ["error al guardar la configuración"];
        avisar(msjs.join(" · "), "err");
      }
    } catch {
      avisar("sin conexión con la API", "err");
    } finally {
      guardandoAjustes = false;
    }
  }

  // --- calendario: carga + escritura ---

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
          fuente = data.length > 0 ? `${data.length} reservas` : "0 reservas";
          // Si la reserva editada está abierta en el panel, refrescala.
          if (seleccionada && modo === "editar") {
            const fresca = data.find((r: Reserva) => r.id === seleccionada!.id);
            if (fresca) seleccionada = fresca;
          }
        }
      }
    } catch {
      fuente = "sin conexión con la API";
    }
  }

  function cargarDraft(r: Reserva) {
    draft = {
      nombre: r.nombre,
      telefono: r.telefono,
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
    confirmandoEliminar = false;
  }

  /** Alta desde el calendario (click/select en un día, S10): 21:00 como
   * hora de arranque del turno noche del bar — se edita en el form. */
  function abrirCrear(fecha: string) {
    seleccionada = null;
    modo = "crear";
    draft = {
      nombre: "",
      telefono: "",
      fecha,
      hora: "21:00",
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

  function reemplazarLocal(actualizada: Reserva) {
    const i = reservas.findIndex((r) => r.id === actualizada.id);
    if (i >= 0) reservas[i] = actualizada;
    else reservas = [...reservas, actualizada];
    if (seleccionada?.id === actualizada.id) {
      seleccionada = actualizada;
      cargarDraft(actualizada);
    }
  }

  /** PUT (crear) o PATCH (editar) del borrador — S10, misma UX del iframe
   * pero contra la API autenticada (/api/reservas). */
  async function guardar() {
    if (!draft || guardando || !usandoAPI) return;
    guardando = true;
    try {
      const cuerpo = JSON.stringify({
        nombre: draft.nombre,
        ...(modo === "crear" ? { telefono: draft.telefono } : {}),
        fecha: draft.fecha,
        hora: draft.hora,
        personas: draft.personas,
        estado: draft.estado,
        detalles: draft.detalles || null,
        enviar_recordatorio: draft.enviar_recordatorio,
      });
      const url = modo === "crear" ? "/api/reservas" : `/api/reservas/${seleccionada?.id}`;
      const res = await fetch(url, {
        method: modo === "crear" ? "POST" : "PATCH",
        headers: { "Content-Type": "application/json" },
        body: cuerpo,
      });
      if (res.ok) {
        const guardada = await res.json();
        avisar(modo === "crear" ? "reserva creada" : "reserva guardada");
        reemplazarLocal(guardada);
        if (modo === "crear") cerrar();
      } else {
        const data = await res.json().catch(() => null);
        const msjs = (data?.detail ?? [])
          .map((e: { loc?: (string | number)[]; msg?: string }) => {
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

  /** DELETE en dos clicks dentro del panel (S10, baja como en el iframe). */
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
      const res = await fetch(`/api/reservas/${id}`, { method: "DELETE" });
      if (res.status === 204) {
        avisar(`reserva #${id} eliminada`);
        reservas = reservas.filter((r) => r.id !== id);
        cerrar();
      } else {
        avisar(`no se pudo eliminar la reserva #${id} (${res.status})`, "err");
      }
    } catch {
      avisar("sin conexión con la API", "err");
    } finally {
      guardando = false;
    }
  }

  /** S9: abre la conversación de Chatwoot donde se gestionó la reserva. */
  async function abrirChat(r: Reserva) {
    try {
      const res = await fetch(`/api/reservas/${r.id}/chat`);
      if (!res.ok) {
        avisar(`no se pudo buscar el chat (${res.status})`, "err");
        return;
      }
      const body = await res.json();
      if (body.url) window.open(body.url, "_blank", "noopener");
      else avisar(body.motivo ?? "conversación no encontrada", "err");
    } catch {
      avisar("sin conexión con la API", "err");
    }
  }

  /** Drag para reprogramar: persiste fecha/hora y refresca el panel. */
  async function reprogramar(id: number, fecha: string, hora: string) {
    if (!usandoAPI) return;
    try {
      const res = await fetch(`/api/reservas/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fecha, hora }),
      });
      if (res.ok) {
        avisar(`reserva #${id} → ${fecha} ${hora}`);
        reemplazarLocal(await res.json());
      } else {
        avisar(`no se pudo mover la reserva #${id} (${res.status})`, "err");
        await cargarReservas();
      }
    } catch {
      avisar("sin conexión con la API", "err");
      await cargarReservas();
    }
  }

  function hhmm(h: string): string {
    return h.slice(0, 5);
  }

  onMount(async () => {
    await cargarReservas();
    // (2) Polling: todos en la oficina ven cambios sin tocar nada.
    pollTimer = setInterval(cargarReservas, POLL_MS);
  });

  // Limpieza del polling al desmontar (evita fetches zombis en dev con HMR).
  onDestroy(() => {
    clearInterval(pollTimer);
    clearTimeout(toastTimer);
    clearTimeout(confirmarTimer);
  });
</script>

<header class="flex items-baseline gap-3 px-5 py-3 border-b border-zinc-200">
  <span class="text-xl font-semibold tracking-wide">Losco</span>
  <span class="text-sm text-zinc-500">Reservas</span>
  <span class="ml-auto text-xs font-medium rounded-full px-2.5 py-1 bg-blue-50 text-blue-700">{fuente}</span>
</header>

<div class="flex flex-wrap items-center gap-x-4 gap-y-1 px-5 py-2 border-b border-zinc-100">
  {#each Object.entries(COLORES_ESTADO) as [estado, c]}
    <span class="inline-flex items-center gap-1.5 text-xs text-zinc-600">
      <i class="w-3 h-3 rounded-sm inline-block" style="background: {c.bg}"></i>{c.label}
    </span>
  {/each}
  <button
    type="button"
    class="ml-auto text-xs rounded-lg px-3 py-1.5 border border-zinc-300 hover:bg-zinc-50 active:bg-zinc-100"
    onclick={() => (ajustesAbierto ? (ajustesAbierto = false) : abrirAjustes())}
  >Mensajes automatizados</button>
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
    onDateClick={(f) => abrirCrear(f)}
    onDateSelect={(ini) => abrirCrear(ini)}
    onEventClick={(r) => abrir(r)}
    onEventDrop={(r, f, h) => reprogramar(r.id, f, h)}
    onEventResize={(r, h) => avisar(`la reserva no tiene hora de fin: ${r.nombre} termina ${h} (sin persistir)`, "err")}
    />
  </div>

  <aside class="space-y-4">
    {#if toast}
      <div
        class="rounded-lg px-3 py-2 text-sm {toast.tipo === 'ok' ? 'text-teal-800 bg-teal-50' : 'text-red-800 bg-red-50'}"
        role="status"
      >{toast.texto}</div>
    {/if}

    {#if ajustesAbierto}
      <section class="rounded-xl border border-zinc-200 p-4 shadow-sm bg-white">
        <div class="flex items-baseline justify-between gap-2 mb-2">
          <h2 class="text-sm font-semibold uppercase tracking-wide text-zinc-500">Mensajes automatizados</h2>
          <button
            type="button"
            class="text-xs rounded px-2 py-0.5 border border-zinc-300 hover:bg-zinc-50"
            onclick={() => (ajustesAbierto = false)}
          >Cerrar</button>
        </div>
        {#if ajustes}
          <form
            class="grid grid-cols-2 gap-3 text-sm"
            onsubmit={(e) => { e.preventDefault(); guardarAjustes(); }}
          >
            <p class="col-span-2 text-xs text-zinc-500">
              T-48h se confirma la reserva; el día del turno se confirma la cantidad de invitados.
            </p>
            <label class="flex flex-col gap-1">
              <span class="text-zinc-500">Hora confirmación (T-48h)</span>
              <input class="rounded border border-zinc-300 px-2 py-1" type="time" bind:value={ajustes.hora_confirmacion} required />
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-zinc-500">Hora invitados (día del turno)</span>
              <input class="rounded border border-zinc-300 px-2 py-1" type="time" bind:value={ajustes.hora_invitados} required />
            </label>
            <label class="col-span-2 flex flex-col gap-1">
              <span class="text-zinc-500">Plantilla confirmación (WhatsApp)</span>
              <input
                class="rounded border border-zinc-300 px-2 py-1"
                type="text"
                list="plantillas-wa"
                bind:value={ajustes.plantilla_confirmacion}
                required
              />
            </label>
            <label class="col-span-2 flex flex-col gap-1">
              <span class="text-zinc-500">Plantilla invitados (WhatsApp)</span>
              <input
                class="rounded border border-zinc-300 px-2 py-1"
                type="text"
                list="plantillas-wa"
                bind:value={ajustes.plantilla_invitados}
                required
              />
            </label>
            <datalist id="plantillas-wa">
              {#each plantillas as p}
                <option value={p.name}></option>
              {/each}
            </datalist>
            {#if plantillasMotivo}
              <p class="col-span-2 text-xs text-amber-700 bg-amber-50 rounded px-2 py-1">{plantillasMotivo}</p>
            {/if}
            <div class="col-span-2">
              <button
                type="submit"
                class="w-full rounded bg-blue-600 px-3 py-1.5 font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                disabled={guardandoAjustes}
              >{guardandoAjustes ? "Guardando…" : "Guardar configuración"}</button>
            </div>
          </form>
        {:else}
          <p class="text-sm text-zinc-400 italic">Cargando configuración…</p>
        {/if}
      </section>
    {/if}

    {#if draft}
      <div class="rounded-xl border border-zinc-200 p-4 shadow-sm bg-white">
        <div class="flex items-baseline justify-between gap-2">
          <h3 class="text-base font-semibold">
            {#if modo === "crear"}Nueva reserva{:else}Reserva Nº {seleccionada?.id}{/if}
          </h3>
          <button
            type="button"
            class="text-xs rounded px-2 py-0.5 border border-zinc-300 hover:bg-zinc-50"
            onclick={cerrar}
          >Cerrar</button>
        </div>
        <form
          class="mt-3 grid grid-cols-2 gap-3 text-sm"
          onsubmit={(e) => { e.preventDefault(); guardar(); }}
        >
          <label class="col-span-2 flex flex-col gap-1">
            <span class="text-zinc-500">Nombre</span>
            <input class="rounded border border-zinc-300 px-2 py-1" type="text" bind:value={draft.nombre} required />
          </label>
          <label class="col-span-2 flex flex-col gap-1">
            <span class="text-zinc-500">Teléfono (intl: +54 9 351 260-2003)</span>
            <input
              class="rounded border border-zinc-300 px-2 py-1 font-mono"
              type="text"
              bind:value={draft.telefono}
              disabled={modo === "editar"}
              required
            />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-zinc-500">Fecha</span>
            <input class="rounded border border-zinc-300 px-2 py-1" type="date" bind:value={draft.fecha} required />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-zinc-500">Hora</span>
            <input class="rounded border border-zinc-300 px-2 py-1" type="time" bind:value={draft.hora} required />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-zinc-500">Personas</span>
            <input class="rounded border border-zinc-300 px-2 py-1" type="number" min="1" max="200" bind:value={draft.personas} required />
          </label>
          <label class="flex flex-col gap-1">
            <span class="text-zinc-500">Estado</span>
            <select class="rounded border border-zinc-300 px-2 py-1" bind:value={draft.estado}>
              {#each ESTADOS as e}
                <option value={e}>{COLORES_ESTADO[e].label}</option>
              {/each}
            </select>
          </label>
          <label class="col-span-2 flex flex-col gap-1">
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
              class="flex-1 rounded bg-blue-600 px-3 py-1.5 font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              disabled={guardando}
            >{guardando ? "Guardando…" : modo === "crear" ? "Crear reserva" : "Guardar"}</button>
            {#if modo === "editar"}
              <button
                type="button"
                class="rounded border px-3 py-1.5 font-medium disabled:opacity-50 {confirmandoEliminar ? 'border-red-600 bg-red-600 text-white' : 'border-red-200 bg-red-50 text-red-700 hover:bg-red-100'}"
                onclick={eliminar}
                disabled={guardando}
              >{confirmandoEliminar ? "¿Seguro? Click de nuevo" : "Eliminar"}</button>
            {/if}
          </div>
        </form>
        {#if modo === "editar" && seleccionada}
          <button
            type="button"
            class="mt-2 w-full rounded border border-zinc-300 px-3 py-1.5 text-sm font-medium hover:bg-zinc-50"
            onclick={() => abrirChat(seleccionada!)}
          >Abrir chat en Chatwoot</button>
        {/if}
      </div>
    {:else if !ajustesAbierto}
      <p class="text-sm text-zinc-400 italic px-1">
        Click en un evento para editarlo · click en un día para crear una reserva.
      </p>
    {/if}
  </aside>
</main>
