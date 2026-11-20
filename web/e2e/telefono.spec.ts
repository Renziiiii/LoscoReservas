import { expect, test, type Page } from "@playwright/test";

/**
 * Smoke tests del input de teléfono (InputTelefono.svelte, intl-tel-input)
 * contra el BUILD compilado servido por `vite preview`. El backend FastAPI
 * NO corre: cada API que el frontend llama se mockea acá con page.route.
 *
 * Lo observable que se pina: el dial code separado ("+54") al lado de la
 * bandera, el selector de países, y que el POST que viaja al backend lleve
 * E.164 completo — el contrato real con app/schemas.py.
 *
 * OJO: FullCalendar v7 NO emite clases fc-* estructurales (las minifica);
 * los selectores estables son los atributos data-date/data-time y el
 * contenido textual de los eventos.
 */

const RESERVA = {
  id: 1,
  nombre: "Ana Ruiz",
  telefono: "+5493210987654",
  // Fecha dinámica: el calendario arranca en el mes actual, una fecha
  // fija en el pasado no renderiza su celda/evento.
  fecha: new Date().toLocaleDateString("sv"),
  hora: "21:00:00",
  personas: 2,
  estado: "pendiente",
  detalles: null,
  enviar_recordatorio: true,
};

/** Mockea las APIs del dashboard: estados configurables + lista vacía. */
async function mockDashboardApi(page: Page) {
  await page.route("**/api/estados*", (route) =>
    route.fulfill({ json: [{ estado: "pendiente", label: "Pendiente", color: "#94a3b8" }] }),
  );
  await page.route("**/api/reservas*", (route) => route.fulfill({ json: [] }));
}

test("dashboard: widget con +54 fijo y el POST lleva E.164", async ({ page }) => {
  await mockDashboardApi(page);

  let bodyTelefono: string | undefined;
  await page.route("**/api/reservas", async (route) => {
    if (route.request().method() === "POST") {
      bodyTelefono = route.request().postDataJSON().telefono;
      return route.fulfill({ json: { ...RESERVA, nombre: "Test" } });
    }
    return route.fulfill({ json: [] });
  });

  await page.goto("/static/calendar/index.html");
  // El panel "Nueva reserva" se abre con un click en un día del mes (el
  // 21 del mes en curso: la vista inicial es ese mes).
  await page.locator(`[data-date='${RESERVA.fecha}']`).click();
  await expect(page.getByText("Nueva reserva")).toBeVisible();

  // El dial code "+54" queda fijo al lado de la bandera (estilo Chatwoot).
  const widget = page.locator(".iti");
  await expect(widget).toBeVisible();
  await expect(widget.locator(".iti__selected-dial-code")).toHaveText("+54");

  // Tipear sólo el número nacional: nadie escribe el 54.
  const input = widget.locator(".iti__tel-input");
  await input.click();
  await input.pressSequentially("93210987654");
  await input.blur();

  // El nombre es requerido: sin él el submit nativo no dispara el POST.
  await page.getByLabel("Nombre").fill("Test");

  // Guardar: el POST al backend debe llevar el E.164 completo.
  await page.getByRole("button", { name: "Crear reserva" }).click();
  expect(bodyTelefono).toBe("+5493210987654");
});

test("dashboard: el selector de países cambia el dial code", async ({ page }) => {
  await mockDashboardApi(page);
  await page.goto("/static/calendar/index.html");
  await page.locator(`[data-date='${RESERVA.fecha}']`).click();

  const widget = page.locator(".iti");
  // Banderita abre el dropdown con buscador; buscar Brasil y elegirlo.
  await widget.locator(".iti__selected-country").click();
  await widget.locator(".iti__search-input").fill("brazil");
  await page.getByRole("option", { name: /Brazil/ }).click();
  await expect(widget.locator(".iti__selected-dial-code")).toHaveText("+55");
});

test("iframe: contacto sin teléfono muestra el hint y permite anotarlo", async ({ page }) => {
  await page.route("**/iframe/estados.json*", (route) =>
    route.fulfill({ json: [{ estado: "pendiente", label: "Pendiente", color: "#94a3b8" }] }),
  );
  await page.route("**/iframe/reservas.json*", (route) => route.fulfill({ json: [] }));

  await page.goto("/static/calendar/iframe.html?key=test");
  // Chatwoot publica el contacto del chat abierto vía postMessage.
  await page.evaluate(() =>
    window.postMessage(
      { event: "appContext", data: { contact: { id: 7, name: "Nico", phone_number: "" } } },
      "*",
    ),
  );
  // Click en un slot del timeGrid (vista semanal inicial) abre el panel.
  // force: la verificación de clickeabilidad de Playwright no pasa sobre
  // el timeGrid de FC v7 (elementFromPoint da null en el lane), pero el
  // click real sí dispara dateClick — verificado manualmente.
  await page.locator("[data-time='21:00:00']").last().click({ force: true });
  await expect(page.getByText("Nueva reserva")).toBeVisible();
  await expect(page.getByText("El contacto no tiene teléfono en Chatwoot")).toBeVisible();

  const widget = page.locator(".iti");
  await expect(widget.locator(".iti__selected-dial-code")).toHaveText("+54");
  const input = widget.locator(".iti__tel-input");
  await input.click();
  await input.pressSequentially("93210987654");
  // El widget formatea en formato nacional argentino mientras se tipea.
  await expect(input).toHaveValue(/9\s?321\s?098-?7654/);
});

test("iframe: contacto con teléfono pre-carga E.164 y bandera AR", async ({ page }) => {
  await page.route("**/iframe/estados.json*", (route) =>
    route.fulfill({ json: [{ estado: "pendiente", label: "Pendiente", color: "#94a3b8" }] }),
  );
  await page.route("**/iframe/reservas.json*", (route) => route.fulfill({ json: [RESERVA] }));

  await page.goto("/static/calendar/iframe.html?key=test");
  await page.evaluate(() =>
    window.postMessage(
      { event: "appContext", data: { contact: { id: 7, name: "Ana", phone_number: "+5493210987654" } } },
      "*",
    ),
  );
  // La reserva del contacto aparece como evento; click lo abre en edición.
  const evento = page.getByText("Ana Ruiz").first();
  await expect(evento).toBeVisible();
  await evento.click();
  await expect(page.getByText("Nueva reserva").or(page.getByText(/Reserva Nº/))).toBeVisible();

  const widget = page.locator(".iti");
  await expect(widget.locator(".iti__selected-dial-code")).toHaveText("+54");
  await expect(widget.locator(".iti__tel-input")).not.toBeEmpty();
});

test("dashboard: screenshots claro y oscuro del panel con el widget", async ({ page }) => {
  await mockDashboardApi(page);
  await page.goto("/static/calendar/index.html");
  await page.locator(`[data-date='${RESERVA.fecha}']`).click();
  await expect(page.locator(".iti")).toBeVisible();
  await page.screenshot({ path: "e2e/artifacts/panel-claro.png" });

  await page.evaluate(() => document.documentElement.classList.add("dark"));
  await page.waitForTimeout(300);
  await page.screenshot({ path: "e2e/artifacts/panel-oscuro.png" });
});

test("dashboard: editar permite cambiar el teléfono (PATCH con E.164)", async ({ page }) => {
  await page.route("**/api/estados*", (route) =>
    route.fulfill({ json: [{ estado: "pendiente", label: "Pendiente", color: "#94a3b8" }] }),
  );
  await page.route("**/api/reservas*", (route) => route.fulfill({ json: [RESERVA] }));

  let patchTelefono: string | undefined;
  await page.route("**/api/reservas/1", async (route) => {
    if (route.request().method() === "PATCH") {
      patchTelefono = route.request().postDataJSON().telefono;
      return route.fulfill({ json: RESERVA });
    }
    return route.fulfill({ json: RESERVA });
  });

  await page.goto("/static/calendar/index.html");
  // La reserva existente aparece como evento; click la abre en edición.
  const evento = page.getByText("Ana Ruiz").first();
  await expect(evento).toBeVisible();
  await evento.click();
  await expect(page.getByText("Reserva Nº 1")).toBeVisible();

  const input = page.locator(".iti__tel-input");
  await expect(input).toBeEnabled(); // ← hoy falla: está disabled

  // Cambiar el teléfono: limpiar y escribir otro número nacional.
  await input.fill("");
  await input.pressSequentially("91155550000");

  await page.getByRole("button", { name: "Guardar", exact: true }).click();
  expect(patchTelefono).toBe("+5491155550000");
});
