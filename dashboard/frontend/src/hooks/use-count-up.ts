import { useEffect, useRef, useState } from "react";

/**
 * Animates a number from 0 to `end` over `duration` ms using easeOutExpo.
 * Returns the current animated value as a formatted string.
 */
export function useCountUp(
  end: number,
  opts?: { duration?: number; formatter?: (n: number) => string },
): string {
  const duration = opts?.duration ?? 800;
  const defaultFormatter = (n: number) => n.toLocaleString();
  const formatter = opts?.formatter ?? defaultFormatter;
  // Keep a stable ref to the formatter so it never triggers re-animation.
  // Ref mutation goes through useEffect (react-hooks/refs forbids
  // writing to refs during render).
  const formatterRef = useRef(formatter);
  useEffect(() => {
    formatterRef.current = formatter;
  }, [formatter]);

  // Compute the initial display from the freshly-resolved formatter
  // rather than reading the ref during render (react-hooks/refs).
  const [display, setDisplay] = useState(() => formatter(0));
  const rafRef = useRef<number>(0);
  const prevEnd = useRef(end);

  useEffect(() => {
    const fmt = formatterRef.current;
    // Skip animation if value hasn't changed
    if (prevEnd.current === end && display !== fmt(0)) return;
    prevEnd.current = end;

    const start = performance.now();
    function tick(now: number) {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      // easeOutExpo
      const eased = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
      const current = Math.round(eased * end);
      setDisplay(formatterRef.current(current));
      if (progress < 1) {
        rafRef.current = requestAnimationFrame(tick);
      }
    }
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [end, duration]);

  return display;
}
