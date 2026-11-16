/**
 * Tipos del spike. En la app real salen de app/schemas.py (Pydantic);
 * acá se replican a mano para no tocar el backend.
 */

export type Estado = "pendiente" | "agendada" | "confirmada";

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
  planta: Planta;
  detalles?: string | null;
}

/** Umbrales de capacidad (regla de Valen, S del feature doc). */
export const CAPACIDAD_AVISO = 80;
export const CAPACIDAD_MAXIMA = 100;

/** Colores por estado: en la app real vienen de la tabla configurable de la DB. */
export const COLORES_ESTADO: Record<Estado, { bg: string; border: string; label: string }> = {
  pendiente: { bg: "#f0a500", border: "#c98a00", label: "Pendiente" },
  agendada: { bg: "#1f93ff", border: "#1668b8", label: "Agendada" },
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

export function estadoOcupacion(total: number): "ok" | "aviso" | "maximo" {
  if (total > CAPACIDAD_MAXIMA) return "maximo";
  if (total >= CAPACIDAD_AVISO) return "aviso";
  return "ok";
}
