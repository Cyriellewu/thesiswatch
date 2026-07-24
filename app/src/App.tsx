import { useState } from "react";
import { BottomNav, type Tab } from "./components/BottomNav";
import { Sidebar } from "./components/Sidebar";
import { TodayScreen } from "./screens/TodayScreen";
import { PortfolioScreen } from "./screens/PortfolioScreen";
import { StockThesisScreen } from "./screens/StockThesisScreen";
import { WhyChangedSheet } from "./components/WhyChangedSheet";
import { EvidenceSheet } from "./components/EvidenceSheet";

export function App() {
  const [tab, setTab] = useState<Tab>("today");
  const [thesisTicker, setThesisTicker] = useState<string | null>(null);
  const [whyTicker, setWhyTicker] = useState<string | null>(null);
  const [evidenceTicker, setEvidenceTicker] = useState<string | null>(null);

  const goTab = (t: Tab) => {
    setThesisTicker(null);
    setTab(t);
  };

  return (
    <div className="bg-app min-h-screen flex">
      <Sidebar tab={tab} onChange={goTab} />

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
              </>
            )}
          </div>
        </main>

        <div className="lg:hidden">
          <BottomNav tab={tab} onChange={goTab} />
        </div>
      </div>

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
