import { forwardRef, type MutableRefObject, type ReactElement } from "react";
import ForceGraph2D, {
  type ForceGraphMethods,
  type ForceGraphProps,
} from "react-force-graph-2d";

import type { GraphCanvasLink, GraphCanvasNode } from "./KnowledgeGraphCanvas";

type ForceGraphCanvasProps = ForceGraphProps<GraphCanvasNode, GraphCanvasLink>;

// react-force-graph-2d 的 ref 类型较宽，这里收窄为项目图谱节点/边类型。
const RawForceGraphCanvas = ForceGraph2D as unknown as (
  props: ForceGraphCanvasProps & {
    ref?: MutableRefObject<ForceGraphMethods<GraphCanvasNode, GraphCanvasLink> | undefined>;
  },
) => ReactElement;

const ForceGraphCanvas = forwardRef<ForceGraphMethods<GraphCanvasNode, GraphCanvasLink> | undefined, ForceGraphCanvasProps>(
  // 单独封装便于懒加载，并隔离第三方组件 ref 类型断言。
  function ForceGraphCanvas(props, ref) {
    return (
      <RawForceGraphCanvas
        {...props}
        ref={ref as MutableRefObject<ForceGraphMethods<GraphCanvasNode, GraphCanvasLink> | undefined>}
      />
    );
  },
);

export default ForceGraphCanvas;
