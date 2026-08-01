import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./app/App";
import AppProviders from "./app/providers/AppProviders";
import "./shared/styles/globals.css";

// 挂载 React 应用根节点，并在最外层注入全局 provider 与路由上下文。
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppProviders>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </AppProviders>
  </React.StrictMode>,
);
