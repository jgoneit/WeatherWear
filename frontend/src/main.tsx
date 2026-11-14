import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Pretendard Variable 자체 호스팅 (npm `pretendard`, OFL). 사용 글자만 받는 dynamic subset.
import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css";
import "./styles/app.css";
import App from "./App";

const root = document.getElementById("root");
if (!root) {
  throw new Error("#root element not found");
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
