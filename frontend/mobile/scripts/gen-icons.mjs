/**
 * Genera los iconos PNG del manifest (192 y 512) sin dependencias: un PNG
 * RGBA escrito a mano (IHDR + IDAT zlib + IEND) con un motivo simple de
 * "ticket" — la PWA debe poder instalarse desde el servidor LAN (§2) sin
 * herramientas de imagen en el repositorio.
 *
 * Uso: npm run icons  (solo hace falta regenerarlo si cambia el motivo)
 */
import { deflateSync } from 'node:zlib';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const outDir = join(root, 'public', 'icons');

const CRC_TABLE = (() => {
  const table = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c;
  }
  return table;
})();

function crc32(buffer) {
  let crc = -1;
  for (const byte of buffer) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ -1) >>> 0;
}

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
}

/** RGBA sólido de un píxel: fondo teal con un "ticket" blanco y líneas grises. */
function pixel(x, y, size) {
  const margin = Math.round(size * 0.18);
  const width = size - 2 * margin;
  // Ticket: rectángulo blanco con borde inferior en dientes (zigzag de 4).
  if (x >= margin && x < margin + width && y >= margin && y < size - margin) {
    const teeth = 4;
    const toothWidth = width / teeth;
    const phase = Math.floor((x - margin) / toothWidth);
    const bottom = size - margin - (phase % 2 === 0 ? 0 : Math.round(size * 0.05));
    if (y >= bottom) return [15, 118, 110, 255]; // teal: el diente forma parte del fondo
    // Líneas del ticket (texto estilizado).
    const rel = (y - margin) / size;
    if (rel > 0.3 && rel < 0.62 && (y - margin) % Math.round(size * 0.09) < Math.round(size * 0.03)) {
      return [148, 163, 184, 255]; // slate-400
    }
    return [255, 255, 255, 255];
  }
  return [15, 118, 110, 255]; // teal-700
}

function png(size) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  const raw = Buffer.alloc(size * (size * 4 + 1));
  for (let y = 0; y < size; y++) {
    const row = y * (size * 4 + 1);
    raw[row] = 0; // filtro None
    for (let x = 0; x < size; x++) {
      const [r, g, b, a] = pixel(x, y, size);
      const at = row + 1 + x * 4;
      raw[at] = r;
      raw[at + 1] = g;
      raw[at + 2] = b;
      raw[at + 3] = a;
    }
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

mkdirSync(outDir, { recursive: true });
for (const size of [192, 512]) {
  writeFileSync(join(outDir, `icon-${size}.png`), png(size));
  console.log(`icon-${size}.png OK`);
}
