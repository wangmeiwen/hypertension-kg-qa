import { forwardRef, type MutableRefObject, type ReactElement } from "react";
import ForceGraph2D, {
  type ForceGraphMethods,
  type ForceGraphProps,
} from "react-force-graph-2d";

import type { GraphCanvasLink, GraphCanvasNode } from "./KnowledgeGraphCanvas";

type ForceGraphCanvasProps = ForceGraphProps<GraphCanvasNode, GraphCanvasLink>;

const RawForceGraphCanvas = ForceGraph2D as unknown as (
  props: ForceGraphCanvasProps & {
    ref?: MutableRefObject<ForceGraphMethods<GraphCanvasNode, GraphCanvasLink> | undefined>;
  },
) => ReactElement;

const ForceGraphCanvas = forwardRef<ForceGraphMethods<GraphCanvasNode, GraphCanvasLink> | undefined, ForceGraphCanvasProps>(
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
