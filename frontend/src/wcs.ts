// Піксель → небо за WCS-заголовком із результату (TAN або TAN-SIP), і небо → висота/азимут.
// Потрібно лише для живого показу координат під курсором; точні значення для зір
// і планет рахує бекенд.

const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;

type Header = Record<string, string | number | boolean>;

export interface Wcs {
  pixToWorld(x: number, y: number): [number, number] | null;
}

export function makeWcs(hdr: Header): Wcs | null {
  const n = (k: string, d?: number): number => {
    const v = hdr[k];
    return typeof v === "number" ? v : d ?? NaN;
  };
  const ctype = String(hdr.CTYPE1 ?? "");
  if (!ctype.includes("TAN")) return null;
  const crpix = [n("CRPIX1"), n("CRPIX2")];
  const crval = [n("CRVAL1") * D2R, n("CRVAL2") * D2R];
  let cd: number[];
  if ("CD1_1" in hdr) {
    cd = [n("CD1_1", 0), n("CD1_2", 0), n("CD2_1", 0), n("CD2_2", 0)];
  } else {
    const c1 = n("CDELT1", 1);
    const c2 = n("CDELT2", 1);
    cd = [n("PC1_1", 1) * c1, n("PC1_2", 0) * c1, n("PC2_1", 0) * c2, n("PC2_2", 1) * c2];
  }
  if (![...crpix, ...crval, ...cd].every(Number.isFinite)) return null;

  const sip = (prefix: "A" | "B") => {
    const order = n(`${prefix}_ORDER`, 0);
    const terms: [number, number, number][] = [];
    for (let p = 0; p <= order; p++)
      for (let q = 0; p + q <= order; q++) {
        const c = n(`${prefix}_${p}_${q}`, 0);
        if (c) terms.push([p, q, c]);
      }
    return terms;
  };
  const A = sip("A");
  const B = sip("B");
  const poly = (terms: [number, number, number][], u: number, v: number) =>
    terms.reduce((acc, [p, q, c]) => acc + c * u ** p * v ** q, 0);

  const [a0, d0] = crval;
  return {
    pixToWorld(x, y) {
      // Пікселі результату рахуються від 0, у FITS — від 1
      const u = x + 1 - crpix[0];
      const v = y + 1 - crpix[1];
      const uu = u + poly(A, u, v);
      const vv = v + poly(B, u, v);
      const xi = (cd[0] * uu + cd[1] * vv) * D2R;
      const eta = (cd[2] * uu + cd[3] * vv) * D2R;
      // Обернена гномонічна проєкція
      const den = Math.cos(d0) - eta * Math.sin(d0);
      const ra = a0 + Math.atan2(xi, den);
      const dec = Math.atan2(Math.sin(d0) + eta * Math.cos(d0), Math.hypot(xi, den));
      return [((ra * R2D) % 360 + 360) % 360, dec * R2D];
    },
  };
}

/** Прецесія J2000 → епоха дати (IAU 1976), щоб висота/азимут не зсувалися на ~0,3°. */
function precess(raDeg: number, decDeg: number, date: Date): [number, number] {
  const T = (date.getTime() / 86400000 + 2440587.5 - 2451545.0) / 36525;
  const as = D2R / 3600;
  const zeta = (2306.2181 * T + 0.30188 * T * T) * as;
  const z = (2306.2181 * T + 1.09468 * T * T) * as;
  const theta = (2004.3109 * T - 0.42665 * T * T) * as;
  const ra = raDeg * D2R;
  const dec = decDeg * D2R;
  const A = Math.cos(dec) * Math.sin(ra + zeta);
  const B = Math.cos(theta) * Math.cos(dec) * Math.cos(ra + zeta) - Math.sin(theta) * Math.sin(dec);
  const C = Math.sin(theta) * Math.cos(dec) * Math.cos(ra + zeta) + Math.cos(theta) * Math.sin(dec);
  return [(Math.atan2(A, B) + z) * R2D, Math.asin(C) * R2D];
}

/** Геометрична висота й азимут (азимут від півночі через схід), градуси. */
export function altAz(raDeg: number, decDeg: number, date: Date, latDeg: number, lonDeg: number): [number, number] {
  const [ra, dec] = precess(raDeg, decDeg, date);
  const jd = date.getTime() / 86400000 + 2440587.5;
  const gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360;
  const ha = (gmst + lonDeg - ra) * D2R;
  const lat = latDeg * D2R;
  const d = dec * D2R;
  const sinAlt = Math.sin(d) * Math.sin(lat) + Math.cos(d) * Math.cos(lat) * Math.cos(ha);
  const alt = Math.asin(Math.max(-1, Math.min(1, sinAlt)));
  const az = Math.atan2(-Math.sin(ha) * Math.cos(d), Math.sin(d) * Math.cos(lat) - Math.cos(d) * Math.sin(lat) * Math.cos(ha));
  return [alt * R2D, ((az * R2D) % 360 + 360) % 360];
}
