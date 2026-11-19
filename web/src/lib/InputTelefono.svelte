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
   * su fondo con --iti-country-selector-bg y hereda el color de texto. */
  :global(.iti) {
    --iti-border-color: var(--line-strong);
    --iti-hover-color: var(--surface-hover);
    --iti-country-selector-bg: var(--surface);
    --iti-icon-color: var(--muted);
    width: 100%;
  }
</style>
