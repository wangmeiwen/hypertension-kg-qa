import { QueryClient } from "@tanstack/react-query";

// 全局 React Query 客户端，统一设置短缓存、禁用窗口聚焦重拉和轻量失败重试。
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
    mutations: {
      retry: 0,
    },
  },
});
