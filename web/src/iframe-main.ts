import "./app.css";
import { mount } from "svelte";
import IframeCalendario from "./lib/IframeCalendario.svelte";

const app = mount(IframeCalendario, { target: document.getElementById("app")! });

export default app;
