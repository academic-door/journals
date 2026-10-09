import { defineConfig } from "astro/config";

const base = process.env.ASTRO_BASE || "/journals";

export default defineConfig({
  site: "https://academic-door.github.io",
  base,
  output: "static",
});
