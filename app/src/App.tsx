import { useState } from "react";
import { BottomNav, type Tab } from "./components/BottomNav";
import { Sidebar } from "./components/Sidebar";
import { TodayScreen } from "./screens/TodayScreen";
import { PortfolioScreen } from "./screens/PortfolioScreen";
import { ThesisDetail } from "./screens/ThesisDetail";

export function App() {
  const [tab, setTab] = useState<Tab>("today");
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [prevConviction, setPrevConviction] = useState<number | undefined>(undefined);

  const goTab = (t: Tab) => {
    setSelectedTicker(null);
    setPrevConviction(undefined);
    setTab(t);
  };

  return (
    <div className="bg-app min-h-screen flex">
      <Sidebar tab={tab} onChange={goTab} />

      <div className="flex-1 flex min-w-0">
        <div className={`flex-1 xl:w-[380px] xl:max-w-[380px] xl:shrink-0 xl:border-r xl:border-[color:var(--hairline)] xl:overflow-y-auto xl:h-screen xl:sticky xl:top-0 flex flex-col ${selectedTicker ? "hidden xl:flex" : "flex"}`}>
          <div className="flex-1">
            {tab === "today" && (
              <TodayScreen
                onOpenThesis={(t, prev) => {
                  setSelectedTicker(t);
                  setPrevConviction(prev);
                }}
                selectedTicker={selectedTicker}
              />
            )}
            {tab === "portfolio" && <PortfolioScreen />}
          </div>
          <div className="lg:hidden">
            <BottomNav tab={tab} onChange={goTab} />
          </div>
        </div>

        <div className={`${selectedTicker ? "flex-1" : "hidden xl:flex"} xl:overflow-y-auto xl:h-screen xl:sticky xl:top-0 xl:max-w-[720px] 2xl:mx-auto`}>
          {selectedTicker ? (
            <ThesisDetail
              ticker={selectedTicker}
              prevConviction={prevConviction}
              onBack={() => {
                setSelectedTicker(null);
                setPrevConviction(undefined);
              }}
            />
          ) : (
            <div className="flex h-full items-center justify-center p-8 text-secondary text-[15px] text-center">
              <div>
                <div className="text-[32px] mb-3 opacity-30">◎</div>
                Select a holding to view its thesis
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
