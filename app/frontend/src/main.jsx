import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

// 应用入口：挂载 React 根节点并渲染主组件。
createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
