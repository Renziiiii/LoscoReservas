<script lang="ts">
  /**
   * Calendario de reservas — FullCalendar 7 vanilla en Svelte 5,
   * con la interactividad de los demos: drag & drop, resize, now
   * indicator, navLinks (click en día → vista diaria) y selección
   * de rangos para crear.
   */
  import dayGridPlugin from "fullcalendar/daygrid";
  import timeGridPlugin from "fullcalendar/timegrid";
  import multiMonthPlugin from "fullcalendar/multimonth";
  import listPlugin from "fullcalendar/list";
  import interactionPlugin from "fullcalendar/interaction";
  import formaThemePlugin from "fullcalendar/themes/forma";
  import esLocale from "fullcalendar/locales/es";
  import type { Calendar, CalendarOptions } from "fullcalendar";
  import FullCalendarHost from "./FullCalendarHost.svelte";
  import {
    coloresEstado,
    estadoOcupacion,
    totalPersonasPorDia,
    type Reserva,
  } from "./reservas";

  interface Props {
    reservas: Reserva[];
    /** Referencia de la lista configurada de estados (T5): su cambio
     * dispara el recomputo de los colores de los eventos. */
    estados?: unknown[];
    onDateClick?: (fechaISO: string) => void;
    onDateSelect?: (inicioISO: string, finISO: string) => void;
    onEventClick?: (r: Reserva) => void;
    onEventDrop?: (r: Reserva, nuevaFecha: string, nuevaHora: string) => void;
    onEventResize?: (r: Reserva, nuevaHoraFin: string) => void;
    /** Cambió el rango visible (navegación o cambio de vista). */
    onDatesSet?: (inicioISO: string, finISO: string) => void;
  }

  let {
    reservas,
    estados = [],
    onDateClick,
    onDateSelect,
    onEventClick,
    onEventDrop,
    onEventResize,
    onDatesSet,
  }: Props = $props();

  let cal = $state<Calendar>();

  const totales = $derived(totalPersonasPorDia(reservas));

  function fechaISO(d: Date): string {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function horaISO(d: Date): string {
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }

  /** La API devuelve la hora con segundos ("21:00:00"); el datetime ISO
   *  local quiere "HH:MM". Normaliza cualquier variante. */
  function hhmm(h: string): string {
    return h.slice(0, 5);
  }

  /** Igual que el iframe (L66): HTML escapado a mano — nombre y detalles
   * son texto de clientes y viajan dentro de un html: custom. */
  function esc(s: string): string {
    return s.replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c]!,
    );
  }

  const events = $derived.by(() => {
    void estados; // T5: los colores dependen de la lista configurada
    return reservas.map((r) => ({
      id: String(r.id),
      // El horario lo renderiza la propia grilla; repetirlo en el título
      // lo mostraba dos veces. El título visible lo arma eventContent:
      // "hora · N 👤 nombre", igual que el iframe de Chatwoot.
      title: `${r.nombre} · ${r.personas}p`,
      start: `${r.fecha}T${hhmm(r.hora)}:00`,
      // `color` alimenta --fc-event-color: es de lo que toma el dot del
      // evento (month/multimonth). backgroundColor/borderColor colorean
      // el bloque en timeGrid. Sin `color`, el dot cae al primary del tema.
      color: coloresEstado(r.estado).bg,
      backgroundColor: coloresEstado(r.estado).bg,
      borderColor: coloresEstado(r.estado).border,
      extendedProps: { reserva: r },
    }));
  });

  const options = $derived.by<CalendarOptions>(() => ({
    plugins: [dayGridPlugin, timeGridPlugin, multiMonthPlugin, listPlugin, interactionPlugin, formaThemePlugin],
    locales: [esLocale],
    locale: "es",
    initialView: "dayGridMonth",
    headerToolbar: {
      left: "prev,next today",
      center: "title",
      right: "multiMonthYear,dayGridMonth,timeGridWeek,timeGridDay,listWeek",
    },
    buttonText: { today: "Hoy", year: "Año", month: "Mes", week: "Semana", day: "Día", list: "Lista" },
    // L14: alto fijo — el calendario ocupa el alto restante de la pantalla
    // y scrollea por dentro (mismo criterio que el iframe), nada se sale
    // del viewport.
    height: "100%",
    dayMaxEvents: true,

    // --- Interactividad (esto es lo que faltaba) ---
    editable: true, // drag de eventos a otro día/hora
    eventResizableFromStart: true, // resize desde ambos extremos
    selectable: true, // seleccionar rango en la grilla para crear
    navLinks: true, // click en header/número de día → vista diaria
    nowIndicator: true, // barrita roja del horario actual (timeGrid)
    scrollTime: "17:00", // la semana abre a la tarde, hora pico del bar

    // Suma de Personas junto al número del día. Ojo: FullCalendar v7
    // RENOMBRÓ el hook — dayCellContent ya no existe (era código muerto:
    // FC lo ignoraba en silencio). El número de fecha se customiza con
    // dayCellTopContent, que REEMPLAZA al número, así que lo incluimos
    // junto al total. El color sigue la regla de Valen (80 aviso / 100
    // máximo; reservas.ts).
    dayCellTopContent(arg) {
      const total = totales.get(fechaISO(arg.date));
      const esHoy = fechaISO(arg.date) === fechaISO(new Date());
      if (!total && !esHoy) return true; // número default del tema
      // Mismo criterio que el iframe: el chip azul del día de HOY se
      // hornea en el HTML (el reset de .losco-num-fecha le quita el fondo
      // al contenedor del tema, y sus clases son hash por build).
      const num = esHoy
        ? `<span class="losco-hoy">${arg.dayNumberText}</span>`
        : arg.dayNumberText;
      if (!total) return { html: num };
      const est = estadoOcupacion(total);
      return {
        html: `<span class="inline-flex items-center gap-1">${num}<span class="losco-cupo losco-cupo--${est}">${total} 👤</span></span>`,
      };
    },

    // La clase sobre el elemento del número: resetea el estilo especial
    // que el tema forma le pone al número de HOY (si no, la info de
    // personas se superponía al número — ver .losco-num-fecha en app.css).
    // NO marcar "hoy" acá: este hook no recibe arg.date.
    dayCellTopInnerClass() {
      return "losco-num-fecha";
    },

    // Semana/Día (timeGrid): el número de fecha vive en la cabecera de la
    // columna, así que la suma va ahí. En Mes los headers son los nombres
    // de día de la semana (sin fecha): sin suma.
    dayHeaderContent(arg) {
      if (!arg.view.type.startsWith("timeGrid")) return true;
      const total = totales.get(fechaISO(arg.date));
      if (!total) return true;
      const est = estadoOcupacion(total);
      return {
        html: `<span class="inline-flex items-center gap-1">${arg.text}<span class="losco-cupo losco-cupo--${est}">${total} 👤</span></span>`,
      };
    },

    // Agenda (listWeek): cada día del listado encabeza con su fecha —
    // la suma va ahí, mismo criterio que en Semana/Día.
    listDayHeaderContent(arg) {
      const total = totales.get(fechaISO(arg.date));
      if (!total) return true;
      const est = estadoOcupacion(total);
      return {
        html: `<span class="inline-flex items-center gap-1">${arg.text}<span class="losco-cupo losco-cupo--${est}">${total} 👤</span></span>`,
      };
    },

    // Render custom del evento, idéntico al iframe (L66): "hora · N 👤
    // nombre" con la 2ª línea de detalles truncada. HTML escapado a mano:
    // nombre/detalles son texto de clientes.
    eventContent(info: any) {
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
    },

    dateClick(info) {
      onDateClick?.(info.dateStr.slice(0, 10));
    },
    select(info) {
      onDateSelect?.(fechaISO(info.start), fechaISO(info.end));
    },
    eventClick(info) {
      onEventClick?.((info.event.extendedProps as { reserva: Reserva }).reserva);
    },
    eventDrop(info) {
      const r = (info.event.extendedProps as { reserva: Reserva }).reserva;
      const d = info.event.start!;
      onEventDrop?.(r, fechaISO(d), horaISO(d));
    },
    eventResize(info) {
      const r = (info.event.extendedProps as { reserva: Reserva }).reserva;
      onEventResize?.(r, horaISO(info.event.end ?? info.event.start!));
    },
    datesSet(info) {
      onDatesSet?.(fechaISO(info.view.activeStart), fechaISO(info.view.activeEnd));
    },
  }));

  // Sincroniza los eventos sin recrear el calendario (opción "live").
  $effect(() => {
    if (cal) cal.setOption("events", $state.snapshot(events) as any);
  });
</script>

<FullCalendarHost {options} bind:cal />
