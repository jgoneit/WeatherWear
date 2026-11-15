import { BrowserRouter, Route, Routes } from "react-router";
import { AppShell } from "./components/layout/AppShell";
import { DemoPage } from "./pages/DemoPage";
import { HistoryPage } from "./pages/HistoryPage";
import { HomePage } from "./pages/HomePage";
import { NotificationsPage } from "./pages/NotificationsPage";
import { OutfitDetailPage } from "./pages/OutfitDetailPage";

/**
 * 라우트 정의. 테스트에서는 MemoryRouter로 감싸 사용한다.
 * Phase 2 기능을 더한 Phase 1 화면(홈의 오늘 브리핑, 데모의 자동화 카드)은 여기서 켠다.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<HomePage dailyBrief />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="notifications" element={<NotificationsPage />} />
        <Route path="outfits/:id" element={<OutfitDetailPage />} />
        <Route path="demo" element={<DemoPage automation />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
