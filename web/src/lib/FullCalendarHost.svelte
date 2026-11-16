<script lang="ts">
  /**
   * Wrapper Svelte 5 para FullCalendar v7 (paquete vanilla).
   * Ciclo de vida completo: `new Calendar(el, options)` en mount y
   * `destroy()` al desmontar. El padre recibe la instancia por
   * `bind:cal` y maneja las opciones "live" (events, view) con setOption.
   */
  import { onDestroy, onMount } from "svelte";
  import { Calendar, type CalendarOptions } from "fullcalendar";

  let {
    options,
    cal = $bindable(),
  }: { options: CalendarOptions; cal?: Calendar } = $props();

  let el = $state<HTMLDivElement>();

  onMount(() => {
    if (!el) return;
    const c = new Calendar(el, options);
    c.render();
    cal = c;
  });

  onDestroy(() => {
    cal?.destroy();
    cal = undefined;
  });
</script>

<div class="fc-host" bind:this={el}></div>

<style>
  .fc-host {
    flex: 1;
    min-width: 0;
  }
</style>
