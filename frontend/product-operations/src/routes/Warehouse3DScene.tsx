import { useMemo, useState } from "react";
import { Canvas } from "@react-three/fiber";
import type { ThreeEvent } from "@react-three/fiber";
import { Grid, Html, Instance, Instances, OrbitControls, Text } from "@react-three/drei";

import type { LocationKind } from "@/api/types";
import { LOCATION_KIND_COLOR, resolveCssColor } from "@/data/locationKindColor";
import { layoutBounds } from "@/lib/warehouseLayout";
import type { WarehouseLayoutNode } from "@/lib/warehouseLayout";

interface Warehouse3DSceneProps {
  layout: WarehouseLayoutNode[];
  selectedId: string | null;
  /** Every location a search is pointing at. Drawn in the accent with a wireframe and a floating
   *  code, so a match is never told apart by colour alone. */
  highlightIds?: ReadonlySet<string> | undefined;
  onSelect: (locationId: string) => void;
}

/** More root labels than this is a wall of text; the hover tooltip names whatever you point at. */
const MAX_ROOT_LABELS = 40;

/** Drawn after every box. With depth testing off for the parts of a highlight that must always be
 *  found, this is what keeps them on top of the boxes rather than at the mercy of draw order. */
const HIGHLIGHT_RENDER_ORDER = 10;

/** Every colour the scene draws with, resolved once from the live stylesheet (light or dark,
 *  whichever is active) rather than written as a literal — a three.js material needs an actual
 *  value, not a Tailwind class, and this is the one place that reads the token that way. */
function useSceneColors() {
  return useMemo(
    () => ({
      grid: resolveCssColor("--color-line"),
      gridSection: resolveCssColor("--color-field-edge"),
      label: resolveCssColor("--color-ink"),
      selection: resolveCssColor("--color-accent"),
      kinds: Object.fromEntries(
        (Object.keys(LOCATION_KIND_COLOR) as LocationKind[]).map((kind) => [
          kind,
          resolveCssColor(LOCATION_KIND_COLOR[kind].cssVar),
        ]),
      ) as Record<LocationKind, string>,
    }),
    [],
  );
}

/**
 * The 3D canvas itself — `@react-three/fiber` + `@react-three/drei`, isolated in its own module
 * so `Warehouse3D.tsx` can lazy-import it only once WebGL is confirmed available (`hasWebGLSupport`)
 * and only inside its error boundary. Every box comes from `layout` — computed by
 * `computeWarehouseLayout` from real `listLocations` data, or by `placementsToSceneNodes` from a
 * floor's published placements — nothing here invents a position, a size or a count.
 *
 * Boxes are instanced per kind (one `<Instances>` draw call per kind present, not one mesh per
 * location — Fulfilment F9's MVP brief, for a warehouse with hundreds of locations) with
 * per-instance position and scale. The click handler reports the location's id; the page that owns
 * the scene opens its drawer, which reads the location's stock.
 */
export function Warehouse3DScene({ layout, selectedId, highlightIds, onSelect }: Warehouse3DSceneProps) {
  const colors = useSceneColors();
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  const byKind = useMemo(() => {
    const groups = new Map<LocationKind, WarehouseLayoutNode[]>();
    for (const node of layout) {
      const list = groups.get(node.location.kind) ?? [];
      list.push(node);
      groups.set(node.location.kind, list);
    }
    return groups;
  }, [layout]);

  // The top of what is drawn: no parent at all, or a parent this layout does not place (a published
  // floor often draws racks and bins but not the zone above them).
  const roots = useMemo(() => {
    const drawn = new Set(layout.map((n) => n.location.id));
    return layout.filter((n) => n.location.parent_id === null || !drawn.has(n.location.parent_id));
  }, [layout]);
  const highlighted = useMemo(
    () => (highlightIds ? layout.filter((n) => highlightIds.has(n.location.id)) : []),
    [layout, highlightIds],
  );
  const selectedNode = useMemo(() => layout.find((n) => n.location.id === selectedId) ?? null, [layout, selectedId]);
  const hoveredNode = useMemo(() => layout.find((n) => n.location.id === hoveredId) ?? null, [layout, hoveredId]);

  const bounds = useMemo(() => layoutBounds(layout), [layout]);
  const centerX = (bounds.minX + bounds.maxX) / 2;
  const centerZ = (bounds.minZ + bounds.maxZ) / 2;
  const span = Math.max(bounds.maxX - bounds.minX, bounds.maxZ - bounds.minZ, bounds.maxY, 4);
  const gridSize = Math.ceil(span * 2 + 8);
  const cameraDistance = span * 1.4 + 6;
  // A real floor is tens of metres across; labels sized for the 1-unit demo boxes would be specks.
  const labelSize = Math.max(0.32, span / 60);

  return (
    <Canvas
      camera={{ position: [centerX + cameraDistance * 0.6, cameraDistance * 0.7, centerZ + cameraDistance * 0.6], fov: 50 }}
      dpr={[1, 2]}
    >
      <ambientLight intensity={0.75} />
      <directionalLight position={[centerX + span, span * 2 + 8, centerZ + span]} intensity={0.9} />
      <Grid
        position={[centerX, 0, centerZ]}
        args={[gridSize, gridSize]}
        cellColor={colors.grid}
        sectionColor={colors.gridSection}
        fadeDistance={gridSize * 1.5}
        infiniteGrid={false}
      />
      <OrbitControls makeDefault enableDamping target={[centerX, 0, centerZ]} />

      {Array.from(byKind.entries()).map(([kind, nodes]) => (
        <Instances key={kind} limit={nodes.length} range={nodes.length}>
          <boxGeometry args={[1, 1, 1]} />
          <meshStandardMaterial color={colors.kinds[kind]} />
          {nodes.map((node) => (
            <Instance
              key={node.location.id}
              position={[node.position[0], node.position[1], node.position[2]]}
              scale={[node.size[0], node.size[1], node.size[2]]}
              onClick={(event: ThreeEvent<MouseEvent>) => {
                event.stopPropagation();
                onSelect(node.location.id);
              }}
              onPointerOver={(event: ThreeEvent<PointerEvent>) => {
                event.stopPropagation();
                setHoveredId(node.location.id);
              }}
              onPointerOut={() => setHoveredId((current) => (current === node.location.id ? null : current))}
            />
          ))}
        </Instances>
      ))}

      {highlighted.map((node) => (
        <group key={node.location.id}>
          <mesh position={[node.position[0], node.position[1], node.position[2]]}>
            <boxGeometry args={[node.size[0] * 1.05, node.size[1] * 1.05, node.size[2] * 1.05]} />
            <meshStandardMaterial color={colors.selection} transparent opacity={0.85} />
          </mesh>
          {/* The outline and the name do not depth-test: a match is always findable, even when a taller
              neighbour (or a container drawn around it) would otherwise hide it. The filled box above
              keeps its depth test, so the matched box itself still sits where it really is. */}
          <mesh
            position={[node.position[0], node.position[1], node.position[2]]}
            renderOrder={HIGHLIGHT_RENDER_ORDER}
          >
            <boxGeometry args={[node.size[0] * 1.25, node.size[1] * 1.25, node.size[2] * 1.25]} />
            <meshBasicMaterial color={colors.label} wireframe depthTest={false} />
          </mesh>
          <Text
            position={[node.position[0], node.position[1] + node.size[1] / 2 + labelSize * 2, node.position[2]]}
            fontSize={labelSize * 1.4}
            color={colors.label}
            anchorX="center"
            anchorY="bottom"
            renderOrder={HIGHLIGHT_RENDER_ORDER}
            material-depthTest={false}
          >
            {`Found: ${node.location.code}`}
          </Text>
        </group>
      ))}

      {selectedNode ? (
        <mesh position={[selectedNode.position[0], selectedNode.position[1], selectedNode.position[2]]}>
          <boxGeometry
            args={[selectedNode.size[0] * 1.15, selectedNode.size[1] * 1.15, selectedNode.size[2] * 1.15]}
          />
          <meshBasicMaterial color={colors.selection} wireframe />
        </mesh>
      ) : null}

      {(roots.length <= MAX_ROOT_LABELS ? roots : []).map((node) => (
        <Text
          key={node.location.id}
          position={[node.position[0], node.position[1] + node.size[1] / 2 + 0.35, node.position[2]]}
          fontSize={labelSize}
          color={colors.label}
          anchorX="center"
          anchorY="bottom"
        >
          {node.location.code}
        </Text>
      ))}

      {hoveredNode ? (
        <Html
          position={[hoveredNode.position[0], hoveredNode.position[1] + hoveredNode.size[1] / 2 + 0.25, hoveredNode.position[2]]}
          center
          pointerEvents="none"
          occlude
        >
          <div className="whitespace-nowrap rounded-chip bg-ink px-2 py-1 text-caption font-semibold text-surface shadow-float">
            {hoveredNode.location.code}
          </div>
        </Html>
      ) : null}
    </Canvas>
  );
}
