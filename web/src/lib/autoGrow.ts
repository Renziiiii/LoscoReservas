/**
 * Acción Svelte: hace que un <textarea> crezca con su contenido.
 *
 * Por qué existe: el campo "Detalles" es una descripción libre (hasta 2000
 * caracteres según app/schemas.py) y con un <input type="text"> de una sola
 * línea el texto se desplazaba en horizontal en vez de envolverse. Un
 * textarea envuelve por sí solo; esta acción agrega el alto automático para
 * que se vea todo lo escrito sin scroll interno.
 *
 * Uso: <textarea rows="1" use:autoGrow></textarea>
 */
export function autoGrow(nodo: HTMLTextAreaElement) {
  const ajustar = () => {
    // auto primero: si no, scrollHeight sólo crece y nunca se encoge.
    nodo.style.height = "auto";
    // offsetHeight - clientHeight = bordes (box-sizing es border-box por el
    // preflight de Tailwind): sin sumarlos queda un scroll interno de ~2px.
    const bordes = nodo.offsetHeight - nodo.clientHeight;
    nodo.style.height = `${nodo.scrollHeight + bordes}px`;
  };

  ajustar();
  nodo.addEventListener("input", ajustar);

  return {
    /** Se llama cuando el valor bindeado cambia por código (p. ej. abrir
     * el panel con una reserva existente). */
    update: ajustar,
    destroy: () => nodo.removeEventListener("input", ajustar),
  };
}
