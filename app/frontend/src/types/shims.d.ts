declare module "d3-force-3d" {
  export function forceCollide(radius?: number | ((node: unknown) => number)): unknown;
  export function forceX(x?: number | ((node: unknown) => number)): unknown;
  export function forceY(y?: number | ((node: unknown) => number)): unknown;
}
