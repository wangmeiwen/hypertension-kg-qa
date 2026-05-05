// d3-force-3d 缺少项目当前使用 API 的完整类型声明，这里补最小 shim。
declare module "d3-force-3d" {
  export function forceCollide(radius?: number | ((node: unknown) => number)): unknown;
  export function forceX(x?: number | ((node: unknown) => number)): unknown;
  export function forceY(y?: number | ((node: unknown) => number)): unknown;
}
