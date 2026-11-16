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
  import interactionPlugin from "fullcalendar/interaction";
  import formaThemePlugin from "fullcalendar/themes/forma";
  import esLocale from "fullcalendar/locales/es";
  import type { Calendar, CalendarOptions } from "fullcalendar";
  import FullCalendarHost from "./FullCalendarHost.svelte";
  import { COLORES_ESTADO, estadoOcupacion, totalPersonasPorDia, type Reserva } from "./reservas";

  interface Props {
    reservas: Reserva[];
    onDateClick?: (fechaISO: string) => void;
    onDateSelect?: (inicioISO: string, finISO: string) => void;
    onEventClick?: (r: Reserva) => void;
    onEventDrop?: (r: Reserva, nuevaFecha: string, nuevaHora: string) => void;
    onEventResize?: (r: Reserva, nuevaHoraFin: string) => void;
  }

  let { reservas, onDateClick, onDateSelect, onEventClick, onEventDrop, onEventResize }: Props = $props();

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

  const events = $derived.by(() =>
    reservas.map((r) => ({
      id: String(r.id),
      title: `${hhmm(r.hora)} ${r.nombre} · ${r.personas}p`,
      start: `${r.fecha}T${hhmm(r.hora)}:00`,
      // `color` alimenta --fc-event-color: es de lo que toma el dot del
      // evento (month/multimonth). backgroundColor/borderColor colorean
      // el bloque en timeGrid. Sin `color`, el dot cae al primary del tema.
      color: COLORES_ESTADO[r.estado].bg,
      backgroundColor: COLORES_ESTADO[r.estado].bg,
      borderColor: COLORES_ESTADO[r.estado].border,
      extendedProps: { reserva: r },
    })),
  );

  const options = $derived.by<CalendarOptions>(() => ({
    plugins: [dayGridPlugin, timeGridPlugin, multiMonthPlugin, interactionPlugin, formaThemePlugin],
    locales: [esLocale],
    locale: "es",
    initialView: "dayGridMonth",
    headerToolbar: {
      left: "prev,next today",
      center: "title",
      right: "multiMonthYear,dayGridMonth,timeGridWeek,timeGridDay",
    },
    buttonText: { today: "Hoy", year: "Año", month: "Mes", week: "Semana", day: "Día" },
    height: "auto",
    dayMaxEvents: true,

    // --- Interactividad (esto es lo que faltaba) ---
    editable: true, // drag de eventos a otro día/hora
    eventResizableFromStart: true, // resize desde ambos extremos
    selectable: true, // seleccionar rango en la grilla para crear
    navLinks: true, // click en header/número de día → vista diaria
    nowIndicator: true, // barrita roja del horario actual (timeGrid)
    scrollTime: "17:00", // la semana abre a la tarde, hora pico del bar

    dayCellContent(arg) {
      const total = totales.get(fechaISO(arg.date));
      if (!total) return true;
      const est = estadoOcupacion(total);
      const txt =
        est === "maximo"
          ? `${total} pers. — ¡supera el máximo!`
          : `${total} pers.${est === "aviso" ? " — cerca del máximo" : ""}`;
      return { html: `<span class="losco-cupo losco-cupo--${est}">${txt}</span>` };
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
  }));

  // Sincroniza los eventos sin recrear el calendario (opción "live").
  $effect(() => {
    if (cal) cal.setOption("events", $state.snapshot(events) as any);
  });
</script>

<FullCalendarHost {options} bind:cal />
