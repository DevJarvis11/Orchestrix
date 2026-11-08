// Plan DAG and trace tree (SPEC 18.1): React Flow with an elkjs layered layout.
import {
	Background,
	Controls,
	type Edge,
	Handle,
	MarkerType,
	type Node,
	type NodeProps,
	Position,
	ReactFlow,
} from "@xyflow/react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { cn } from "../lib/utils";
import { StatusBadge, toneOf } from "./StatusBadge";

// elkjs is large: load it the first time a graph is laid out.
let elkInstance: Promise<{
	layout: (g: ElkGraph) => Promise<ElkGraph>;
}> | null = null;
function getElk() {
	elkInstance ??= import("elkjs/lib/elk.bundled.js").then(
		(m) => new m.default(),
	);
	return elkInstance;
}
interface ElkGraph {
	id: string;
	layoutOptions?: Record<string, string>;
	children?: {
		id: string;
		width?: number;
		height?: number;
		x?: number;
		y?: number;
	}[];
	edges?: { id: string; sources: string[]; targets: string[] }[];
}
const NODE_W = 220;
const NODE_H = 72;

export interface FlowItem {
	id: string;
	title: string;
	subtitle?: string;
	status?: string | null;
	extra?: ReactNode;
}

type FlowNodeData = {
	item: FlowItem;
	selected: boolean;
	onSelect: (id: string) => void;
};

const BORDER: Record<string, string> = {
	pending: "border-st-pending",
	running: "border-st-running",
	review: "border-st-review",
	human: "border-st-human",
	approved: "border-st-approved",
	failed: "border-st-failed",
	skipped: "border-st-skipped border-dashed",
};

function ItemNode({ data }: NodeProps<Node<FlowNodeData>>) {
	const { item, selected, onSelect } = data;
	return (
		<button
			type="button"
			onClick={() => onSelect(item.id)}
			aria-pressed={selected}
			aria-label={`${item.title}${item.status ? `, ${item.status.replace(/_/g, " ")}` : ""}`}
			className={cn(
				"flex h-[72px] w-[220px] flex-col justify-between rounded-md border-2 bg-surface px-2.5 py-1.5 text-left shadow-sm",
				BORDER[toneOf(item.status)],
				selected && "ring-2 ring-accent",
			)}
		>
			<Handle type="target" position={Position.Left} className="!bg-border" />
			<span className="truncate text-sm font-semibold">{item.title}</span>
			{item.subtitle && (
				<span className="truncate text-xs text-muted">{item.subtitle}</span>
			)}
			<span className="flex items-center gap-1">
				{item.status && <StatusBadge status={item.status} />}
				{item.extra}
			</span>
			<Handle type="source" position={Position.Right} className="!bg-border" />
		</button>
	);
}

const nodeTypes = { item: ItemNode };

export function FlowGraph({
	items,
	edges,
	selected,
	onSelect,
	direction = "RIGHT",
	height = 420,
}: {
	items: FlowItem[];
	edges: { source: string; target: string }[];
	selected?: string | null;
	onSelect: (id: string) => void;
	direction?: "RIGHT" | "DOWN";
	height?: number;
}) {
	const [positions, setPositions] = useState<
		Record<string, { x: number; y: number }>
	>({});
	const shape = `${items.map((i) => i.id).join(",")}|${edges.map((e) => `${e.source}>${e.target}`).join(",")}`;

	const latest = useRef({ items, edges });
	latest.current = { items, edges };

	useEffect(() => {
		let live = true;
		const { items: layoutItems, edges: layoutEdges } = latest.current;
		void getElk()
			.then((elk) =>
				elk.layout({
					id: `root:${shape}`,
					layoutOptions: {
						"elk.algorithm": "layered",
						"elk.direction": direction,
						"elk.spacing.nodeNode": "28",
						"elk.layered.spacing.nodeNodeBetweenLayers": "56",
					},
					children: layoutItems.map((i) => ({
						id: i.id,
						width: NODE_W,
						height: NODE_H,
					})),
					edges: layoutEdges.map((e, n) => ({
						id: `e${n}`,
						sources: [e.source],
						targets: [e.target],
					})),
				}),
			)
			.then((g) => {
				if (!live) return;
				const out: Record<string, { x: number; y: number }> = {};
				for (const c of g.children ?? [])
					out[c.id] = { x: c.x ?? 0, y: c.y ?? 0 };
				setPositions(out);
			});
		return () => {
			live = false;
		};
	}, [shape, direction]);

	const nodes: Node<FlowNodeData>[] = items.map((item) => ({
		id: item.id,
		type: "item",
		position: positions[item.id] ?? { x: 0, y: 0 },
		data: { item, selected: item.id === selected, onSelect },
		draggable: false,
		connectable: false,
	}));
	const flowEdges: Edge[] = edges.map((e, n) => ({
		id: `e${n}`,
		source: e.source,
		target: e.target,
		markerEnd: { type: MarkerType.ArrowClosed },
	}));

	// Render once laid out (fitView runs on mount), and refit when the shape changes.
	const laidOut = items.every((i) => positions[i.id]);
	return (
		<div
			style={{ height }}
			className="rounded-md border border-border bg-surface-2"
		>
			{laidOut && (
				<ReactFlow
					key={shape}
					nodes={nodes}
					edges={flowEdges}
					nodeTypes={nodeTypes}
					fitView
					nodesFocusable
					proOptions={{ hideAttribution: true }}
					minZoom={0.2}
				>
					<Background />
					<Controls showInteractive={false} />
				</ReactFlow>
			)}
		</div>
	);
}
