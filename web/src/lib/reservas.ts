/**
 * Tipos del spike. En la app real salen de app/schemas.py (Pydantic);
 * acá se replican a mano para no tocar el backend.
 */

export type Estado = "pendiente" | "confirmada" | "cancelada";

export type Planta = "alta" | "baja" | null;

export interface Reserva {
  id: number;
  nombre: string;
  telefono: string;
  /** ISO date, ej: 2026-11-20 */
  fecha: string;
  /** ISO time, ej: 21:30 */
  hora: string;
  personas: number;
  estado: Estado;
  detalles?: string | null;
  enviar_recordatorio?: boolean;
  planta?: Planta;
}

/** Umbrales de capacidad (regla de Valen, S del feature doc). */
export const CAPACIDAD_AVISO = 80;
export const CAPACIDAD_MAXIMA = 100;

/** Colores por estado: en la app real vienen de la tabla configurable de la DB. */
export const COLORES_ESTADO: Record<Estado, { bg: string; border: string; label: string }> = {
  pendiente: { bg: "#f0a500", border: "#c98a00", label: "Pendiente" },
  cancelada: { bg: "#e54666", border: "#c22b4e", label: "Cancelada" },
  confirmada: { bg: "#00b877", border: "#009161", label: "Confirmada (oficial)" },
};

/** Total de personas reservadas por fecha (ISO). */
export function totalPersonasPorDia(reservas: Reserva[]): Map<string, number> {
  const totales = new Map<string, number>();
  for (const r of reservas) {
    totales.set(r.fecha, (totales.get(r.fecha) ?? 0) + r.personas);
  }
  return totales;
}

export const ESTADOS: Estado[] = ["pendiente", "confirmada", "cancelada"];

export function estadoOcupacion(total: number): "ok" | "aviso" | "maximo" {
  if (total > CAPACIDAD_MAXIMA) return "maximo";
  if (total >= CAPACIDAD_AVISO) return "aviso";
  return "ok";
}

const GRIS = [156, 163, 175]; // gray-400

/** Mezcla un color hex hacia gris (L64: eventos de otros contactos).
 * Se aplica como backgroundColor/borderColor inline, que FullCalendar
 * pinta por su cuenta — la distinción no depende de que cargue una
 * clase CSS utilitaria. factor 0 = color original, 1 = gris puro. */
export function atenuarColor(hex: string, factor = 0.55): string {
  const n = hex.replace("#", "");
  const canales = [0, 2, 4].map((i) => parseInt(n.slice(i, i + 2), 16));
  const mezcla = canales.map((c, i) => Math.round(c + (GRIS[i] - c) * factor));
  return `#${mezcla.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}
