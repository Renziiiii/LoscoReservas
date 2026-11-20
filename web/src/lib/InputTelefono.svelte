<script lang="ts">
  /**
   * Input de teléfono con banderita y dial code separado (estilo Chatwoot).
   *
   * Envuelve @intl-tel-input/svelte (intl-tel-input v29) y expone un único
   * binding `value` en formato E.164 ("+5493210987654") — exactamente lo que
   * el backend normaliza y guarda (app/schemas.py) y lo que Chatwoot manda
   * en contact.phone_number. El usuario tipea sólo el número nacional: el
   * "+54" (o el dial code del país elegido) queda fijo al lado de la
   * bandera, así nadie lo escribe más.
   *
   * Componente controlado: el `value` inicial (típicamente el E.164 del
   * contacto de Chatwoot) setea la bandera; cada cambio del usuario vuelve
   * como E.164 (o "" si queda vacío/inválido) vía onChangeNumber.
   */
  import IntlTelInput from "@intl-tel-input/svelte";
  import "intl-tel-input/styles";

  let {
    value = $bindable(""),
    disabled = false,
    inputId = undefined,
  }: {
    value?: string;
    disabled?: boolean;
    inputId?: string;
  } = $props();
</script>

<IntlTelInput
  {value}
  {disabled}
  initialCountry="ar"
  separateDialCode
  autoPlaceholder="aggressive"
  loadUtils={() => import("intl-tel-input/utils")}
  onChangeNumber={(n) => (value = n)}
  inputProps={{ id: inputId, autocomplete: "tel", required: true }}
/>

<style>
  /* La librería expone sus colores como variables CSS (éstas son todas las
   * que tiene): las atamos a los roles semánticos de tokens.css, así el
   * widget se integra al resto del formulario y el dark mode sale gratis
   * (las mismas variables cambian de valor bajo .dark). El dropdown pinta
   * su fondo con --iti-country-selector-bg y hereda el color de texto.
   *
   * El CAJA la dibuja el contenedor .iti — mismo borde, radio y fondo que
   * los inputs hermanos (rounded border border-line-strong bg-surface);
   * el selector inline de país lleva su propio borde, que se anula para
   * no duplicar el recuadro. */
  :global(.iti) {
    --iti-border-color: var(--line-strong);
    --iti-hover-color: var(--surface-hover);
    --iti-country-selector-bg: var(--surface);
    --iti-icon-color: var(--muted);
    width: 100%;
    border: 1px solid var(--line-strong);
    border-radius: 0.25rem; /* Tailwind "rounded", como los otros inputs */
    background: var(--surface);
    /* Mismo padding-block que py-1 en los inputs hermanos: sin esto el
     * widget queda 8px más bajito que el resto de los campos del form. */
    padding-block: var(--spacing);
    /* El flag button NO lleva padding horizontal: apoya contra el borde
     * izquierdo, como el widget de Chatwoot. */
  }
  :global(.iti--inline-country-selector .iti__country-selector) {
    border: none; /* el recuadro lo pone .iti; el selector no duplica */
  }
</style>
