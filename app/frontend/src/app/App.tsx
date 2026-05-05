import AppRouter from "./router";

// 应用根组件只负责挂载路由树，Provider 与 BrowserRouter 在 main.tsx 注入。
export default function App() {
  return <AppRouter />;
}
