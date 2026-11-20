/**
 * Tipos y helpers compartidos del calendario (dashboard e iframe).
 *
 * T5 (tags-estados-sync): los estados ya NO son un enum fijo — la lista
 * configurable vive en la tabla `estados_reserva` del backend y se carga
 * con `cargarEstados()` (GET /api/estados en el dashboard, /iframe/
 * estados.json?key=… en el iframe). `coloresEstado`/`nombreDeEstado`
 * resuelven SIEMPRE un estilo: estado base → color conocido; estado con
 * `color` configurado → ese color; estado desconocido → color
 * determinista por hash del slug (nunca undefined).
 */

export type Estado = string;

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

export interface EstiloEstado {
  bg: string;
  border: string;
  label: string;
}

/** Una fila de la tabla configurada (los endpoints del backend devuelven
 * exactamente estos campos — /api/estados agrega id y es_base). */
export interface EstadoConfig {
  nombre: string;
  slug: string;
  etiqueta_chatwoot: string | null;
  color: string | null;
  envia_recordatorio: boolean;
}

/** Umbrales de capacidad (regla de Valen, S del feature doc). */
export const CAPACIDAD_AVISO = 80;
export const CAPACIDAD_MAXIMA = 100;

/** Colores de los 3 estados base: fallback permanente si el backend no
 * responde o aún no cargó la lista configurable. */
export const COLORES_ESTADO: Record<string, EstiloEstado> = {
  pendiente: { bg: "#f0a500", border: "#c98a00", label: "Pendiente" },
  cancelada: { bg: "#e54666", border: "#c22b4e", label: "Cancelada" },
  confirmada: {
    bg: "#00b877",
    border: "#009161",
    label: "Confirmada (oficial)",
  },
};

/** Fallback de estados (misma lista que siembra el backend). */
export const ESTADOS: Estado[] = ["pendiente", "confirmada", "cancelada"];

/** Paleta para estados custom sin color configurado: determinista por
 * hash del slug (mismo slug → mismo color en cualquier cliente). */
const PALETA: { bg: string; border: string }[] = [
  { bg: "#6366f1", border: "#4f46e5" }, // indigo
  { bg: "#0ea5e9", border: "#0284c7" }, // sky
  { bg: "#a855f7", border: "#9333ea" }, // purple
  { bg: "#f97316", border: "#ea580c" }, // orange
  { bg: "#14b8a6", border: "#0d9488" }, // teal
  { bg: "#ec4899", border: "#db2777" }, // pink
];

let configurados: EstadoConfig[] | null = null;

function hashSlug(slug: string): number {
  let hash = 0;
  for (let i = 0; i < slug.length; i++)
    hash = (hash * 31 + slug.charCodeAt(i)) >>> 0;
  return hash;
}

function hexValido(valor: string): boolean {
  return /^#[0-9a-fA-F]{6}$/.test(valor);
}

/** Oscurece un hex para el borde (misma relación visual que los base). */
function oscurecer(hex: string, factor = 0.75): string {
  const n = hex.replace("#", "");
  const canales = [0, 2, 4].map((i) => parseInt(n.slice(i, i + 2), 16));
  const oscuro = canales.map((c) => Math.round(c * factor));
  return `#${oscuro.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

function filasNormalizadas(rows: unknown): EstadoConfig[] {
  if (!Array.isArray(rows)) return [];
  const filas: EstadoConfig[] = [];
  for (const row of rows) {
    if (typeof row !== "object" || row === null) continue;
    const r = row as Record<string, unknown>;
    if (typeof r.slug !== "string" || typeof r.nombre !== "string") continue;
    filas.push({
      nombre: r.nombre,
      slug: r.slug,
      etiqueta_chatwoot:
        typeof r.etiqueta_chatwoot === "string" ? r.etiqueta_chatwoot : null,
      color: typeof r.color === "string" ? r.color : null,
      envia_recordatorio: r.envia_recordatorio === true,
    });
  }
  return filas;
}

/** Descarga la lista configurable del backend (una vez por carga del
 * componente). Devuelve true si quedó cargada. Nunca lanza: si falla,
 * el fallback de los 3 estados base sigue valiendo. */
export async function cargarEstados(url: string): Promise<boolean> {
  try {
    const res = await fetch(url);
    if (!res.ok) return false;
    const filas = filasNormalizadas(await res.json());
    if (filas.length === 0) return false;
    configurados = filas;
    return true;
  } catch {
    return false;
  }
}

/** El nombre (label) del estado: configurado > base > el slug crudo. */
export function nombreDeEstado(slug: string): string {
  return (
    configurados?.find((c) => c.slug === slug)?.nombre ??
    COLORES_ESTADO[slug]?.label ??
    slug
  );
}

/** Los slugs configurados (o el fallback base si el backend no cargó). */
export function nombresDeEstados(): Estado[] {
  return configurados?.map((c) => c.slug) ?? ESTADOS;
}

/** El color del estado: color configurado > base > paleta por hash.
 * Nunca devuelve undefined — estados desconocidos siempre pintan. */
export function coloresEstado(slug: string): { bg: string; border: string } {
  const colorConfig = configurados?.find((c) => c.slug === slug)?.color;
  if (colorConfig && hexValido(colorConfig)) {
    return { bg: colorConfig, border: oscurecer(colorConfig) };
  }
  const base = COLORES_ESTADO[slug];
  if (base) return { bg: base.bg, border: base.border };
  return { ...PALETA[hashSlug(slug) % PALETA.length] };
}

export function estiloEstado(slug: string): EstiloEstado {
  return { ...coloresEstado(slug), label: nombreDeEstado(slug) };
}

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

const GRIS = [156, 163, 175]; // gray-400

/** Mezcla un color hex hacia gris (L64: eventos de otros contactos).
 * Se aplica como backgroundColor/borderColor inline, que FullCalendar
 * pinta por su cuenta — la distinción no depende de que cargue una
 * clase CSS utilitaria. factor 0 = color original, 1 = gris puro. */
export function atenuarColor(hex: string, factor = 0.85): string {
  const n = hex.replace("#", "");
  const canales = [0, 2, 4].map((i) => parseInt(n.slice(i, i + 2), 16));
  const mezcla = canales.map((c, i) => Math.round(c + (GRIS[i] - c) * factor));
  return `#${mezcla.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}
