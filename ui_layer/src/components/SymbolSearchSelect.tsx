import { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";

interface Props {
  symbols: string[];
  value: string;
  onChange: (symbol: string) => void;
  placeholder?: string;
}

export function SymbolSearchSelect({ symbols, value, onChange, placeholder }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const wrapRef = useRef<HTMLDivElement>(null);

  const filtered = query
    ? symbols.filter((s) => s.toLowerCase().includes(query.toLowerCase()))
    : symbols;

  useEffect(() => {
    const onMouseDown = (event: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, []);

  return (
    <div className="symbol-search" ref={wrapRef}>
      <Search size={13} />
      <input
        value={open ? query : value}
        placeholder={placeholder || "搜索交易品种"}
        onFocus={() => {
          setOpen(true);
          setQuery("");
        }}
        onChange={(e) => setQuery(e.target.value)}
      />
      {open && (
        <div className="symbol-dropdown">
          {filtered.slice(0, 200).map((s) => (
            <button
              key={s}
              className={`symbol-option ${s === value ? "active" : ""}`}
              onMouseDown={(e) => {
                e.preventDefault();
                onChange(s);
                setOpen(false);
              }}
            >
              {s}
            </button>
          ))}
          {filtered.length === 0 && <div className="empty">未找到匹配品种</div>}
        </div>
      )}
    </div>
  );
}

