// Copies the browser libraries out of node_modules into static/vendor/ so the
// app serves them from its own origin. No CDN: the map has to keep working on
// a phone with a flaky connection at a petrol station, and a third-party
// script origin is one more thing that can fail or watch.
import { copyFile, mkdir, readdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';

const files = [
  ['node_modules/leaflet/dist/leaflet.js', 'static/vendor/leaflet/leaflet.js'],
  ['node_modules/leaflet/dist/leaflet.css', 'static/vendor/leaflet/leaflet.css'],
  ['node_modules/htmx.org/dist/htmx.min.js', 'static/vendor/htmx/htmx.min.js'],
];

// Leaflet's stylesheet references these by relative URL, so they have to land
// beside it or every marker and control renders as a broken image.
const imageDir = 'node_modules/leaflet/dist/images';

for (const [from, to] of files) {
  await mkdir(dirname(to), { recursive: true });
  await copyFile(from, to);
}

await mkdir('static/vendor/leaflet/images', { recursive: true });
for (const name of await readdir(imageDir)) {
  await copyFile(join(imageDir, name), join('static/vendor/leaflet/images', name));
}

console.log(`vendored ${files.length} files + leaflet images`);
