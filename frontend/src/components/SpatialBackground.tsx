import { useEffect, useRef } from "react";

interface Star {
  x: number; // -1..1
  y: number; // -1..1
  z: number; // 0 (far) .. 1 (near)
  r: number;
  tw: number;
  hue: number;
}

function seeded(n: number): number {
  const x = Math.sin(n * 9999) * 10000;
  return x - Math.floor(x);
}

const STARS: Star[] = Array.from({ length: 260 }, (_, i) => ({
  x: seeded(i) * 2 - 1,
  y: seeded(i + 1) * 2 - 1,
  z: seeded(i + 2),
  r: seeded(i + 3) * 1.6 + 0.4,
  tw: seeded(i + 4) * Math.PI * 2,
  hue: seeded(i + 5) * 60 + 180,
}));

/**
 * Full-viewport starfield + perspective-grid backdrop that gives the spatial UI its sense of
 * depth. Purely decorative (aria-hidden, pointer-events: none); every pixel of data still comes
 * from the API. Honours prefers-reduced-motion by rendering a single static frame, and pauses
 * the animation loop while the tab is hidden to avoid burning cycles.
 */
export function SpatialBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    let raf = 0;
    let t = 0;

    // Pointer parallax target/current (smoothed in the loop, like usePointerDepth).
    let targetX = 0;
    let targetY = 0;
    let curX = 0;
    let curY = 0;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(window.innerWidth * dpr);
      canvas.height = Math.floor(window.innerHeight * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const drawFrame = () => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      ctx.clearRect(0, 0, w, h);

      // Nebula wash — two soft radial glows.
      const g1 = ctx.createRadialGradient(w * 0.2, h * 0.3, 0, w * 0.2, h * 0.3, w * 0.7);
      g1.addColorStop(0, "rgba(90, 70, 200, 0.10)");
      g1.addColorStop(1, "rgba(90, 70, 200, 0)");
      ctx.fillStyle = g1;
      ctx.fillRect(0, 0, w, h);

      const g2 = ctx.createRadialGradient(w * 0.8, h * 0.7, 0, w * 0.8, h * 0.7, w * 0.6);
      g2.addColorStop(0, "rgba(40, 120, 160, 0.08)");
      g2.addColorStop(1, "rgba(40, 120, 160, 0)");
      ctx.fillStyle = g2;
      ctx.fillRect(0, 0, w, h);

      // Perspective grid floor.
      const horizon = h * 0.55;
      ctx.strokeStyle = "rgba(120, 140, 200, 0.08)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let i = -20; i <= 20; i++) {
        const x = w / 2 + i * w * 0.06;
        ctx.moveTo(x, horizon);
        ctx.lineTo(w / 2 + (x - w / 2) * 0.02, h);
      }
      ctx.stroke();

      ctx.beginPath();
      for (let i = 0; i <= 40; i++) {
        const y = horizon + ((i * (h - horizon)) / 40);
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
      }
      ctx.stroke();

      // Stars with depth + pointer parallax (nearer stars drift more).
      const cx = w / 2;
      const cy = h / 2;
      for (const s of STARS) {
        const depth = 0.4 + s.z * 0.6;
        const px = cx + (s.x + curX * 0.12 * depth) * w * 0.5 * (1 - s.z * 0.5);
        const py = cy + (s.y + curY * 0.12 * depth) * h * 0.5 * (1 - s.z * 0.5) - h * 0.1;
        const twinkle = 0.5 + 0.5 * Math.sin(t * 2 + s.tw);
        const alpha = (0.2 + s.z * 0.8) * twinkle;
        ctx.fillStyle = `hsla(${s.hue}, 80%, 80%, ${alpha})`;
        ctx.beginPath();
        ctx.arc(px, py, s.r * depth, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    const loop = () => {
      t += 0.004;
      // Smooth the pointer toward its target for a gentle drift.
      curX += (targetX - curX) * 0.06;
      curY += (targetY - curY) * 0.06;
      drawFrame();
      raf = requestAnimationFrame(loop);
    };

    const start = () => {
      if (reduce.matches) {
        drawFrame(); // one static frame, no animation
      } else if (!raf) {
        raf = requestAnimationFrame(loop);
      }
    };
    const stop = () => {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
    };

    const onMove = (e: MouseEvent) => {
      targetX = (e.clientX / window.innerWidth) * 2 - 1;
      targetY = (e.clientY / window.innerHeight) * 2 - 1;
    };
    const onLeave = () => {
      targetX = 0;
      targetY = 0;
    };
    const onVisibility = () => {
      if (document.hidden) stop();
      else start();
    };
    const onMotionChange = () => {
      stop();
      start();
    };

    resize();
    start();
    window.addEventListener("resize", resize);
    window.addEventListener("mousemove", onMove);
    document.addEventListener("mouseleave", onLeave);
    document.addEventListener("visibilitychange", onVisibility);
    reduce.addEventListener("change", onMotionChange);

    return () => {
      stop();
      window.removeEventListener("resize", resize);
      window.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseleave", onLeave);
      document.removeEventListener("visibilitychange", onVisibility);
      reduce.removeEventListener("change", onMotionChange);
    };
  }, []);

  // Placement, opacity and z-index come from the #space-canvas rule in app.css.
  return <canvas ref={canvasRef} id="space-canvas" aria-hidden="true" />;
}
