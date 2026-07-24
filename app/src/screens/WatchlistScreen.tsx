import { useEffect, useState } from "react";
import { allHoldings as mockHoldings } from "../data/mock";
import { fetchToday } from "../data/api";
import { StatusPill } from "../components/primitives";
import type { Holding } from "../types";

export function WatchlistScreen({ onOpenThesis }: { onOpenThesis: (t: string) => void }) {
  const [allHoldings, setAllHoldings] = useState<Holding[]>(mockHoldings);
  useEffect(() => {
    let live = true;
    fetchToday().then((env) => {
      if (!live || !env.data) return;
      const b = env.data;
      const all = [...b.needsAttention, ...b.worthWatching, ...b.noMaterialChange];
      if (all.length) setAllHoldings(all);
    });
    return () => { live = false; };
  }, []);
  return (
    <div className="px-5 lg:px-8 pt-6 pb-12">
      <h1 className="text-[26px] lg:text-[32px] font-bold">Watchlist</h1>
      <p className="text-secondary text-[14px] mt-1">Your holdings, sorted by conviction change.</p>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2 mt-5">
        {[...allHoldings]
          .sort((a, b) => Math.abs(b.conviction - b.prevConviction) - Math.abs(a.conviction - a.prevConviction))
          .map((h) => {
            const delta = h.conviction - h.prevConviction;
            return (
              <button
                key={h.ticker}
                onClick={() => onOpenThesis(h.ticker)}
                className="w-full flex items-center justify-between bg-surface rounded-card px-4 py-3 text-left min-h-[44px]"
              >
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-[15px] font-semibold">{h.ticker}</span>
                    <StatusPill status={h.status} size="sm" />
                  </div>
                  <div className="text-[12px] text-secondary mt-0.5">{h.name}</div>
                </div>
                <div className="tnum text-right">
                  <div className="text-[15px] font-semibold">{h.conviction}</div>
                  <div
                    className="text-[12px] font-medium"
                    style={{ color: delta === 0 ? "var(--neutral)" : delta > 0 ? "var(--pos)" : "var(--neg)" }}
                  >
                    {delta > 0 ? "+" : ""}
                    {delta}
                  </div>
                </div>
              </button>
            );
          })}
      </div>
    </div>
  );
}
