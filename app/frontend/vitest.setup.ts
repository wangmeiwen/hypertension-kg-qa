import "@testing-library/jest-dom/vitest";

import { vi } from "vitest";

// jsdom 不提供 ResizeObserver，这里用最小 mock 支撑图谱画布和响应式组件测试。
class ResizeObserverMock {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}

Object.defineProperty(window, "ResizeObserver", {
  writable: true,
  configurable: true,
  value: ResizeObserverMock,
});

Object.defineProperty(navigator, "clipboard", {
  // 测试环境 mock 剪贴板，避免复制消息时依赖浏览器权限。
  writable: true,
  configurable: true,
  value: {
    writeText: vi.fn().mockResolvedValue(undefined),
  },
});

Object.defineProperty(HTMLElement.prototype, "scrollTo", {
  // jsdom 没有真实滚动实现，聊天页自动滚动用 no-op mock 覆盖。
  writable: true,
  configurable: true,
  value: vi.fn(),
});
