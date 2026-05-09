import { useEffect, useRef } from "react";

const COLORS = [
  [255, 255, 255],
];

function mkParticle(w, h) {
  const angle = Math.random() * Math.PI * 2;
  const speed = Math.random() * 0.28 + 0.04;
  const ci = Math.floor(Math.random() * COLORS.length);
  return {
    x: Math.random() * w,
    y: Math.random() * h,
    vx: Math.cos(angle) * speed,
    vy: Math.sin(angle) * speed,
    r: Math.random() * 1.2 + 0.4,
    base: Math.random() * 0.55 + 0.12,
    pulseOff: Math.random() * Math.PI * 2,
    pulseSpd: Math.random() * 0.018 + 0.006,
    color: COLORS[ci],
  };
}

export default function ParticleField({ count = 60 }) {
  const ref = useRef(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    let raf;
    let particles = [];
    let t = 0;

    const resize = () => {
      canvas.width  = window.innerWidth;
      canvas.height = window.innerHeight;
      particles = Array.from({ length: count }, () => mkParticle(canvas.width, canvas.height));
    };

    window.addEventListener("resize", resize);
    resize();

    const draw = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      t++;

      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;

        if (p.x <= 0 || p.x >= canvas.width)  { p.vx *= -1; p.x = Math.max(0, Math.min(canvas.width,  p.x)); }
        if (p.y <= 0 || p.y >= canvas.height)  { p.vy *= -1; p.y = Math.max(0, Math.min(canvas.height, p.y)); }

        const alpha = p.base + Math.sin(t * p.pulseSpd + p.pulseOff) * 0.18;
        const [r, g, b] = p.color;

        // glow halo
        const grad = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.r * 3.5);
        grad.addColorStop(0, `rgba(${r},${g},${b},${(alpha * 0.1).toFixed(3)})`);
        grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r * 3.5, 0, Math.PI * 2);
        ctx.fill();

        // core
        ctx.globalAlpha = alpha;
        ctx.fillStyle = `rgb(${r},${g},${b})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      raf = requestAnimationFrame(draw);
    };

    draw();

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, [count]);

  return (
    <canvas
      ref={ref}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 0,
        pointerEvents: "none",
        opacity: 0.88,
      }}
    />
  );
}
