import { useState } from "react";
import { BottomNav, type Tab } from "./components/BottomNav";
import { Sidebar } from "./components/Sidebar";
import { TodayScreen } from "./screens/TodayScreen";
import { PortfolioScreen } from "./screens/PortfolioScreen";
import { WatchlistScreen } from "./screens/WatchlistScreen";
import { StockThesisScreen } from "./screens/StockThesisScreen";
import { WhyChangedSheet } from "./components/WhyChangedSheet";
import { EvidenceSheet } from "./components/EvidenceSheet";

export function App() {
  const [tab, setTab] = useState<Tab>("today");
  const [thesisTicker, setThesisTicker] = useState<string | null>(null);
  const [whyTicker, setWhyTicker] = useState<string | null>(null);
  const [evidenceTicker, setEvidenceTicker] = useState<string | null>(null);
  const [askOpen, setAskOpen] = useState(false);

  const goTab = (t: Tab) => {
    setThesisTicker(null);
    setTab(t);
  };

  return (
    <div className="bg-app min-h-screen flex">
      <Sidebar tab={tab} onChange={goTab} onAsk={() => setAskOpen(true)} />

      <div className="flex-1 flex flex-col min-w-0 relative">
        <main className="flex-1 pt-safe">
          <div className="mx-auto w-full max-w-6xl">
            {thesisTicker ? (
              <StockThesisScreen
                ticker={thesisTicker}
                onBack={() => setThesisTicker(null)}
                onEvidence={setEvidenceTicker}
              />
            ) : (
              <>
                {tab === "today" && (
                  <TodayScreen
                    onOpenThesis={setThesisTicker}
                    onWhyChanged={setWhyTicker}
                    onEvidence={setEvidenceTicker}
                  />
                )}
                {tab === "portfolio" && <PortfolioScreen />}
                {tab === "watchlist" && <WatchlistScreen onOpenThesis={setThesisTicker} />}
              </>
            )}
          </div>
        </main>

        {/* Floating Ask + bottom tabs: narrow screens only */}
        {!thesisTicker && (
          <button
            onClick={() => setAskOpen(true)}
            className="lg:hidden fixed right-4 bottom-[76px] rounded-chip shadow-card px-4 min-h-[44px] text-[14px] font-semibold z-30"
            style={{ background: "var(--text)", color: "var(--surface)" }}
          >
            Ask
          </button>
        )}
        <div className="lg:hidden">
          <BottomNav tab={tab} onChange={goTab} />
        </div>
      </div>

      <AskSheet open={askOpen} surface={tab} onClose={() => setAskOpen(false)} />
      <WhyChangedSheet
        ticker={whyTicker}
        onClose={() => setWhyTicker(null)}
        onOpenEvidence={(t) => {
          setWhyTicker(null);
          setEvidenceTicker(t);
        }}
      />
      <EvidenceSheet ticker={evidenceTicker} onClose={() => setEvidenceTicker(null)} />
    </div>
  );
}

function AskSheet({ open, surface, onClose }: { open: boolean; surface: Tab; onClose: () => void }) {
  const suggestions =
    surface === "portfolio"
      ? ["What is my biggest hidden risk?", "Which holdings overlap the most?", "What changes if I add $500 to QQQ?"]
      : ["Why did MSFT's thesis strengthen?", "What is the strongest counterargument?", "What would invalidate the thesis?"];
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center sm:justify-center">
      <button className="absolute inset-0 bg-black/35 animate-fade-in" onClick={onClose} />
      <div className="relative w-full sm:max-w-[460px] bg-surface rounded-t-sheet sm:rounded-sheet p-5 pb-safe sm:pb-5 animate-sheet-in shadow-card">
        <div className="flex justify-center mb-3 sm:hidden">
          <div className="h-1.5 w-10 rounded-chip" style={{ background: "var(--hairline)" }} />
        </div>
        <h2 className="text-[17px] font-bold mb-1">Ask Alpha</h2>
        <p className="text-secondary text-[13px] mb-3">
          Contextual to {surface === "portfolio" ? "your portfolio" : "today"} — answers cite their source.
        </p>
        <div className="space-y-2">
          {suggestions.map((s) => (
            <div key={s} className="bg-surface-2 rounded-card px-4 py-3 text-[15px]">{s}</div>
          ))}
        </div>
        <button
          onClick={onClose}
          className="mt-4 w-full rounded-card py-3 text-[15px] font-medium min-h-[44px]"
          style={{ background: "var(--surface-2)" }}
        >
          Close
        </button>
      </div>
    </div>
  );
}
