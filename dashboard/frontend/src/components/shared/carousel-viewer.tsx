import { useState, useCallback, type KeyboardEvent } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface CarouselImage {
  url: string;
  alt?: string;
}

interface CarouselViewerProps {
  images: CarouselImage[];
  className?: string;
}

export function CarouselViewer({ images, className }: CarouselViewerProps) {
  const [rawIndex, setCurrentIndex] = useState(0);
  // Clamp index inline — no useEffect needed
  const currentIndex = images.length > 0 ? Math.min(rawIndex, images.length - 1) : 0;

  const goToPrev = useCallback(() => {
    setCurrentIndex((i) => (i > 0 ? i - 1 : images.length - 1));
  }, [images.length]);

  const goToNext = useCallback(() => {
    setCurrentIndex((i) => (i < images.length - 1 ? i + 1 : 0));
  }, [images.length]);

  const goToSlide = useCallback((index: number) => {
    setCurrentIndex(index);
  }, []);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "ArrowLeft") {
        goToPrev();
        e.preventDefault();
      } else if (e.key === "ArrowRight") {
        goToNext();
        e.preventDefault();
      }
    },
    [goToPrev, goToNext],
  );

  if (images.length === 0) {
    return null;
  }

  return (
    <div
      className={cn("relative select-none outline-none", className)}
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      {/* Image viewport */}
      <div className="relative overflow-hidden rounded-lg bg-muted/20">
        <div
          className="flex transition-transform duration-300 ease-in-out"
          style={{ transform: `translateX(-${currentIndex * 100}%)` }}
        >
          {images.map((image, index) => (
            <div
              key={index}
              className="w-full flex-shrink-0"
            >
              <img
                src={image.url}
                alt={image.alt ?? `Slide ${index + 1}`}
                className="w-full object-contain"
                draggable={false}
              />
            </div>
          ))}
        </div>
      </div>

      {/* Navigation arrows */}
      {images.length > 1 && (
        <>
          <Button
            variant="ghost"
            size="icon-sm"
            className="absolute left-2 top-1/2 -translate-y-1/2 bg-background/80 backdrop-blur-sm hover:bg-background/90"
            onClick={goToPrev}
          >
            <ChevronLeft className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            className="absolute right-2 top-1/2 -translate-y-1/2 bg-background/80 backdrop-blur-sm hover:bg-background/90"
            onClick={goToNext}
          >
            <ChevronRight className="size-4" />
          </Button>
        </>
      )}

      {/* Dot indicators */}
      {images.length > 1 && (
        <div className="flex items-center justify-center gap-1.5 pt-3">
          {images.map((_, index) => (
            <button
              key={index}
              type="button"
              className={cn(
                "size-2 rounded-full transition-colors",
                index === currentIndex
                  ? "bg-foreground"
                  : "bg-muted-foreground/30 hover:bg-muted-foreground/50"
              )}
              onClick={() => goToSlide(index)}
              aria-label={`Go to slide ${index + 1}`}
            />
          ))}
        </div>
      )}
    </div>
  );
}
