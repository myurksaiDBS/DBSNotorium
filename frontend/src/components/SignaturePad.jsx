import { useEffect, useRef, useImperativeHandle, forwardRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Eraser } from "lucide-react";

const SignaturePad = forwardRef(({ initialData = "", height = 180 }, ref) => {
  const canvasRef = useRef(null);
  const drawing = useRef(false);
  const last = useRef({ x: 0, y: 0 });
  const [empty, setEmpty] = useState(!initialData);

  const getCtx = () => canvasRef.current?.getContext("2d");

  const setupCanvas = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    const ctx = getCtx();
    ctx.scale(dpr, dpr);
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "#00317A";
  };

  useEffect(() => {
    setupCanvas();
    if (initialData) {
      const img = new Image();
      img.onload = () => {
        const ctx = getCtx();
        const canvas = canvasRef.current;
        const rect = canvas.getBoundingClientRect();
        ctx.drawImage(img, 0, 0, rect.width, rect.height);
      };
      img.src = initialData;
    }
    const onResize = () => setupCanvas();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
    // eslint-disable-next-line
  }, []);

  const point = (e) => {
    const rect = canvasRef.current.getBoundingClientRect();
    const t = e.touches?.[0] || e;
    return { x: t.clientX - rect.left, y: t.clientY - rect.top };
  };

  const start = (e) => {
    e.preventDefault();
    drawing.current = true;
    last.current = point(e);
  };
  const move = (e) => {
    if (!drawing.current) return;
    e.preventDefault();
    const p = point(e);
    const ctx = getCtx();
    ctx.beginPath();
    ctx.moveTo(last.current.x, last.current.y);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    last.current = p;
    setEmpty(false);
  };
  const end = () => { drawing.current = false; };

  useImperativeHandle(ref, () => ({
    clear() {
      const ctx = getCtx();
      const canvas = canvasRef.current;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      setEmpty(true);
    },
    isEmpty() { return empty; },
    toDataURL() {
      if (empty) return "";
      return canvasRef.current.toDataURL("image/png");
    },
  }));

  return (
    <div>
      <div className="sig-pad" style={{ height }}>
        <canvas
          ref={canvasRef}
          data-testid="signature-canvas"
          style={{ width: "100%", height: "100%", display: "block" }}
          onMouseDown={start}
          onMouseMove={move}
          onMouseUp={end}
          onMouseLeave={end}
          onTouchStart={start}
          onTouchMove={move}
          onTouchEnd={end}
        />
      </div>
      <div className="mt-2 flex justify-end">
        <Button type="button" variant="ghost" size="sm"
          data-testid="signature-clear-btn"
          onClick={() => { const ctx = getCtx(); ctx.clearRect(0,0,canvasRef.current.width, canvasRef.current.height); setEmpty(true); }}>
          <Eraser className="w-3.5 h-3.5 mr-1" /> Clear signature
        </Button>
      </div>
    </div>
  );
});

export default SignaturePad;
