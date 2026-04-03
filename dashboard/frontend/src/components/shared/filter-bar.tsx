import { useMemo, useCallback, useState, useEffect, useRef } from "react";
import { useQueryStates, parseAsString } from "nuqs";
import { Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

interface FilterOption {
  value: string;
  label: string;
}

interface FilterDefinition {
  key: string;
  label: string;
  options: FilterOption[];
}

interface FilterBarProps {
  filters: FilterDefinition[];
  search?: boolean;
  searchPlaceholder?: string;
  className?: string;
}

export function useFilterParams(filters: FilterDefinition[]) {
  const parsersMap = useMemo(() => {
    const parsers: Record<string, typeof parseAsString> = {};
    for (const filter of filters) {
      parsers[filter.key] = parseAsString;
    }
    parsers["q"] = parseAsString;
    return parsers;
  }, [filters]);

  const [params, setParams] = useQueryStates(parsersMap);

  return { params, setParams };
}

export function FilterBar({
  filters,
  search = false,
  searchPlaceholder = "Search...",
  className,
}: FilterBarProps) {
  const { params, setParams } = useFilterParams(filters);
  const [localSearch, setLocalSearch] = useState(params["q"] ?? "");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const toggleFilter = useCallback(
    (key: string, value: string) => {
      void setParams((prev) => ({
        ...prev,
        [key]: prev[key] === value ? "" : value,
      }));
    },
    [setParams]
  );

  const handleSearchChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const value = e.target.value;
      setLocalSearch(value);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        void setParams((prev) => ({ ...prev, q: value }));
      }, 400);
    },
    [setParams]
  );

  // Sync local state when URL params change externally
  useEffect(() => {
    setLocalSearch(params["q"] ?? "");
  }, [params]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      <div className="flex flex-wrap items-center gap-4">
        {filters.map((filter) => (
          <div key={filter.key} className="flex items-center gap-1.5">
            <span className="text-xs font-medium text-muted-foreground">
              {filter.label}:
            </span>
            <div className="flex flex-wrap gap-1">
              {filter.options.map((option) => {
                const isActive = params[filter.key] === option.value;
                return (
                  <Badge
                    key={option.value}
                    variant={isActive ? "default" : "outline"}
                    className={cn(
                      "cursor-pointer select-none transition-colors",
                      !isActive && "hover:bg-accent"
                    )}
                    onClick={() => toggleFilter(filter.key, option.value)}
                  >
                    {option.label}
                  </Badge>
                );
              })}
            </div>
          </div>
        ))}

        {search && (
          <div className="relative ml-auto min-w-[200px]">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <Input
              value={localSearch}
              onChange={handleSearchChange}
              placeholder={searchPlaceholder}
              className="pl-8 h-8 text-sm"
            />
          </div>
        )}
      </div>
    </div>
  );
}
