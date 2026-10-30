// Фон: зорі, що мерехтять і повільно обертаються навколо полюса, й зрідка метеори.

interface BgStar {
  r: number; // відстань від полюса, частка діагоналі
  a: number; // кут, рад
  size: number;
  phase: number;
  speed: number;
  hue: number;
}

interface Meteor {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
}

export function startStarfield(canvas: HTMLCanvasElement) {
  const ctx = canvas.getContext("2d")!;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let w = 0;
  let h = 0;
  let dpr = 1;
  let stars: BgStar[] = [];
  const meteors: Meteor[] = [];

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = canvas.clientWidth;
    h = canvas.clientHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const n = Math.round((w * h) / 2600);
    stars = Array.from({ length: n }, () => {
      const m = Math.random() ** 3; // більшість зір тьмяні
      return {
        r: Math.sqrt(Math.random()) * 1.1,
        a: Math.random() * Math.PI * 2,
        size: 0.35 + m * 1.6,
        phase: Math.random() * Math.PI * 2,
        speed: 0.6 + Math.random() * 2.2,
        hue: [210, 220, 45, 30, 200][Math.floor(Math.random() * 5)],
      };
    });
  }

  function frame(t: number) {
    const sec = t / 1000;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    // Полюс світу ліворуч угорі за кадром: зорі описують дуги, як на довгій витримці
    const px = w * 0.08;
    const py = -h * 0.15;
    const diag = Math.hypot(w, h);
    const rot = reduced ? 0 : sec * 0.004;
    for (const st of stars) {
      const a = st.a + rot;
      const x = px + Math.cos(a) * st.r * diag;
      const y = py + Math.sin(a) * st.r * diag;
      if (x < -2 || y < -2 || x > w + 2 || y > h + 2) continue;
      const tw = reduced ? 0.8 : 0.55 + 0.45 * Math.sin(sec * st.speed + st.phase);
      ctx.fillStyle = `hsla(${st.hue}, 80%, 88%, ${0.25 + 0.6 * tw})`;
      ctx.beginPath();
      ctx.arc(x, y, st.size * (0.8 + 0.2 * tw), 0, Math.PI * 2);
      ctx.fill();
    }

    if (!reduced && Math.random() < 0.004 && meteors.length < 2) {
      const ang = Math.PI * (0.15 + Math.random() * 0.2);
      const v = 9 + Math.random() * 6;
      meteors.push({ x: Math.random() * w, y: Math.random() * h * 0.4, vx: Math.cos(ang) * v, vy: Math.sin(ang) * v, life: 1 });
    }
    for (let i = meteors.length - 1; i >= 0; i--) {
      const m = meteors[i];
      const g = ctx.createLinearGradient(m.x, m.y, m.x - m.vx * 9, m.y - m.vy * 9);
      g.addColorStop(0, `rgba(255,255,255,${0.9 * m.life})`);
      g.addColorStop(1, "rgba(255,255,255,0)");
      ctx.strokeStyle = g;
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(m.x, m.y);
      ctx.lineTo(m.x - m.vx * 9, m.y - m.vy * 9);
      ctx.stroke();
      m.x += m.vx;
      m.y += m.vy;
      m.life -= 0.022;
      if (m.life <= 0) meteors.splice(i, 1);
    }
    if (!reduced) requestAnimationFrame(frame);
  }

  resize();
  window.addEventListener("resize", () => {
    resize();
    if (reduced) frame(0);
  });
  requestAnimationFrame(frame);
}
