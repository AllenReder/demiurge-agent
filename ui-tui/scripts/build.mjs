import { build } from "esbuild";

await build({
  entryPoints: ["src/entry.tsx"],
  bundle: true,
  minify: true,
  platform: "node",
  format: "esm",
  target: "node20",
  outfile: "dist/entry.js",
  banner: {
    js: 'import { createRequire } from "module"; const require = createRequire(import.meta.url);',
  },
});
