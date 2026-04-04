#!/usr/bin/env python3
"""
Compute clean ReactFlow positions for Tdarr flow JSON files,
then generate SVG overviews for README documentation.

Algorithm:
  1. Topological sort (Kahn's) over the node graph.
  2. For each node, Y = max(column_cursor[x], max_predecessor_bottom + GAP).
  3. Column (X) is determined by a per-flow lookup table.
  4. Heights are estimated from node name line-count + plugin type.
  5. The layout is split into horizontal sections for a rectangular shape.
  6. All edges are drawn as SVG paths with orthogonal routing.
  7. Cross-section edges route through the gaps between sections.
  8. An SVG overview is written to images/ for README documentation.
"""

from __future__ import annotations

import html
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable

FLOWS_DIR  = Path(__file__).parent.parent / "flows"
IMAGES_DIR = Path(__file__).parent.parent / "images"

GAP               = 90     # vertical gap between nodes (px)
NODE_WIDTH        = 420    # Tdarr node width
BEND_RADIUS       = 10     # rounded corner radius for orthogonal edges
SECTION_GAP       = 350    # horizontal gap between wrapped sections
SVG_DISPLAY_WIDTH = 4000   # SVG rendered width (px)


# ── Height estimation ──────────────────────────────────────────────────────────

def estimate_height(node: dict) -> int:
    lines      = node.get("name", "").count("\n") + 1
    is_comment = node.get("pluginName") == "comment"
    base       = 60 if is_comment else 50
    min_h      = 100 if is_comment else 80
    return max(min_h, base + lines * 20)


# ── Core layout engine ─────────────────────────────────────────────────────────

def compute_positions(
    flow: dict, col_map: dict[str, int]
) -> tuple[dict[str, dict], list[str], dict[str, list[str]]]:
    """Return ({node_id -> {x, y}}, topo_order, out_adj)."""
    nodes    = {n["id"]: n for n in flow["flowPlugins"]}
    edges    = flow["flowEdges"]
    out_adj  : dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int]       = {nid: 0 for nid in nodes}

    for e in edges:
        if e["source"] not in nodes or e["target"] not in nodes:
            continue
        out_adj[e["source"]].append(e["target"])
        in_degree[e["target"]] += 1

    queue: deque[str] = deque(
        nid for nid in nodes if in_degree[nid] == 0
    )
    topo: list[str] = []
    while queue:
        nid = queue.popleft()
        topo.append(nid)
        for target in out_adj[nid]:
            in_degree[target] -= 1
            if in_degree[target] == 0:
                queue.append(target)

    if len(topo) < len(nodes):
        missing = set(nodes) - set(topo)
        print(f"    WARNING: cycle — {len(missing)} nodes excluded: "
              f"{missing}")

    col_cursor: dict[int, int] = defaultdict(int)
    min_y:      dict[str, int] = defaultdict(int)
    positions:  dict[str, dict] = {}

    for nid in topo:
        node = nodes.get(nid)
        if node is None:
            continue
        x = col_map.get(nid, 0)
        y = max(col_cursor[x], min_y[nid])
        y = (y + 9) // 10 * 10

        h = estimate_height(node)
        positions[nid] = {"x": x, "y": y}
        col_cursor[x]  = max(col_cursor[x], y + h + GAP)

        bottom = y + h
        for target in out_adj[nid]:
            min_y[target] = max(min_y[target], bottom + GAP)

    return positions, topo, dict(out_adj)


# ── Section splitting ─────────────────────────────────────────────────────────

def _find_break_points(
    positions: dict[str, dict],
    edges: list[dict],
    col_map: dict[str, int],
    num_sections: int,
    main_col: int = 0,
) -> list[float]:
    """Find y-values to split the layout into sections.

    Prefers M-chain edges with the fewest cross-section edge violations,
    closest to the ideal even split points.
    """
    if num_sections <= 1:
        return []

    all_ys = [p["y"] for p in positions.values()]
    total = max(all_ys) if all_ys else 0
    target_splits = [total * i / num_sections for i in range(1, num_sections)]

    # Collect candidate break points: midpoints of M-chain edges
    candidates: list[tuple[float, int]] = []
    for e in edges:
        sid, tid = e["source"], e["target"]
        if sid not in positions or tid not in positions:
            continue
        if col_map.get(sid) != main_col or col_map.get(tid) != main_col:
            continue
        mid_y = (positions[sid]["y"] + positions[tid]["y"]) / 2
        # Count edges that would cross this y-level
        cross = 0
        for e2 in edges:
            s2, t2 = e2["source"], e2["target"]
            if s2 not in positions or t2 not in positions:
                continue
            sy = positions[s2]["y"]
            ty_ = positions[t2]["y"]
            if (sy < mid_y < ty_) or (ty_ < mid_y < sy):
                if positions[s2]["x"] != main_col or positions[t2]["x"] != main_col:
                    cross += 1
        candidates.append((mid_y, cross))

    if not candidates:
        return target_splits

    # For each target split, find the candidate with lowest cross count
    # within a reasonable distance
    breaks: list[float] = []
    for target_y in target_splits:
        best = min(
            candidates,
            key=lambda c: (c[1], abs(c[0] - target_y)),
        )
        # Only use if reasonably close (within 40% of section height)
        section_h = total / num_sections
        if abs(best[0] - target_y) < section_h * 0.4:
            breaks.append(best[0])
        else:
            breaks.append(target_y)

    return sorted(set(breaks))


def _compute_section_width(col_map: dict[str, int]) -> int:
    vals = list(col_map.values())
    return max(vals) - min(vals) + NODE_WIDTH


def _assign_sections(
    positions: dict[str, dict],
    break_ys: list[float],
    col_map: dict[str, int],
    topo: list[str],
    edges: list[dict],
    main_col: int = 0,
) -> dict[str, int]:
    """Assign each node to a section based on break points."""
    num_sections = len(break_ys) + 1
    node_section: dict[str, int] = {}

    # Assign M-chain nodes by y position
    for nid in topo:
        if nid not in positions:
            continue
        if col_map.get(nid) == main_col:
            y = positions[nid]["y"]
            sec = 0
            for i, by in enumerate(break_ys):
                if y > by:
                    sec = i + 1
            node_section[nid] = sec

    # Propagate to non-M nodes via predecessors
    pred: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        pred[e["target"]].append(e["source"])

    for nid in topo:
        if nid not in node_section and nid in positions:
            sects = [node_section[p] for p in pred[nid]
                     if p in node_section]
            node_section[nid] = max(sects, default=0)

    return node_section


def apply_wrapping(
    positions: dict[str, dict],
    node_section: dict[str, int],
    break_ys: list[float],
    section_width: int,
) -> dict[str, dict]:
    """Shift nodes into horizontal sections."""
    shift = section_width + SECTION_GAP

    # Compute y-offset per section (min y of nodes in that section)
    section_y_min: dict[int, float] = {}
    for nid, pos in positions.items():
        sec = node_section.get(nid, 0)
        y = pos["y"]
        if sec not in section_y_min or y < section_y_min[sec]:
            section_y_min[sec] = y

    # Ensure all sections have a y_min
    for s in range(len(break_ys) + 1):
        if s not in section_y_min:
            section_y_min[s] = break_ys[s - 1] if s > 0 else 0

    return {
        nid: {
            "x": pos["x"] + node_section.get(nid, 0) * shift,
            "y": max(0, pos["y"] - section_y_min.get(
                node_section.get(nid, 0), 0)),
        }
        for nid, pos in positions.items()
    }


# ── SVG generation ─────────────────────────────────────────────────────────────

PLUGIN_COLORS: dict[str, str] = {
    "inputFile":                            "#90CAF9",
    "comment":                              "#E8EAF6",
    "setFlowVariable":                      "#CE93D8",
    "checkFileExtension":                   "#FFB74D",
    "checkVideoCodec":                      "#FFB74D",
    "checkStreamProperty":                  "#FFB74D",
    "checkChannelCount":                    "#FFB74D",
    "checkFlowVariable":                    "#FFB74D",
    "checkNodeHardwareEncoder":             "#FFB74D",
    "checkVideoResolution":                 "#FFB74D",
    "checkFilePath":                        "#FFB74D",
    "checkFileNameIncludes":                "#FFB74D",
    "checkOverallBitrate":                  "#FFB74D",
    "runHealthCheck":                       "#EF9A9A",
    "ffmpegCommandStart":                   "#A5D6A7",
    "ffmpegCommandSetContainer":            "#C8E6C9",
    "ffmpegCommandRemoveSubtitles":         "#C8E6C9",
    "ffmpegCommandRemoveStreamByProperty":  "#C8E6C9",
    "ffmpegCommandRemoveDataStreams":        "#C8E6C9",
    "ffmpegCommandSetVideoEncoder":         "#66BB6A",
    "ffmpegCommandCustomArguments":         "#C8E6C9",
    "ffmpegCommandEnsureAudioStream":       "#C8E6C9",
    "ffmpegCommandRorderStreams":           "#C8E6C9",
    "ffmpegCommandExecute":                 "#2E7D32",
    "compareFileSizeRatio":                 "#FFF176",
    "compareFileDurationRatio":             "#FFF176",
    "checkSSIMScore":                       "#FFF176",
    "replaceOriginalFile":                  "#EF5350",
    "requireReview":                        "#FF8A65",
    "webRequest":                           "#4FC3F7",
    "notifyRadarrOrSonarr":                 "#4FC3F7",
    "onFlowError":                          "#B0BEC5",
    "resetFlowError":                       "#B0BEC5",
    "failFlow":                             "#B0BEC5",
}
LIGHT_TEXT_PLUGINS = {"ffmpegCommandExecute", "replaceOriginalFile"}
EDGE_COLORS = {"1": "#66BB6A", "2": "#EF5350", "err1": "#90A4AE"}
DEFAULT_COLOR = "#ECEFF1"


def _polyline_path(points: list[tuple[float, float]], r: float) -> str:
    """Generate SVG path for an orthogonal polyline with rounded corners."""
    if len(points) < 2:
        return ""
    if len(points) == 2:
        return (f"M{points[0][0]:.1f},{points[0][1]:.1f} "
                f"L{points[1][0]:.1f},{points[1][1]:.1f}")

    parts = [f"M{points[0][0]:.1f},{points[0][1]:.1f}"]
    for i in range(1, len(points) - 1):
        px, py = points[i - 1]
        cx, cy = points[i]
        nx, ny = points[i + 1]

        in_len = abs(cx - px) + abs(cy - py)
        out_len = abs(nx - cx) + abs(ny - cy)
        ar = min(r, in_len / 2, out_len / 2)
        if ar < 1:
            parts.append(f"L{cx:.1f},{cy:.1f}")
            continue

        idx = (1 if cx > px else -1) if abs(cx - px) > 0.1 else 0
        idy = (1 if cy > py else -1) if abs(cy - py) > 0.1 else 0
        odx = (1 if nx > cx else -1) if abs(nx - cx) > 0.1 else 0
        ody = (1 if ny > cy else -1) if abs(ny - cy) > 0.1 else 0

        bx = cx - idx * ar if idx else cx
        by = cy - idy * ar if idy else cy
        ax = cx + odx * ar if odx else cx
        ay = cy + ody * ar if ody else cy

        parts.append(f"L{bx:.1f},{by:.1f}")
        parts.append(f"Q{cx:.1f},{cy:.1f} {ax:.1f},{ay:.1f}")

    last = points[-1]
    parts.append(f"L{last[0]:.1f},{last[1]:.1f}")
    return " ".join(parts)


def _svg_marker(handle_key: str, color: str) -> str:
    mid = (handle_key.replace("err1", "err")
           .replace("1", "ok").replace("2", "no"))
    return (
        f'<marker id="arr-{mid}" markerWidth="5" markerHeight="5"'
        f' refX="4" refY="2.5" orient="auto">'
        f'<path d="M0,0 L0,5 L5,2.5 z" fill="{color}"/></marker>'
    )


def _find_clear_y(
    mid_y: float,
    x1: float, x2: float,
    node_rects: list[tuple[float, float, float, float]],
    margin: float = 15,
) -> float:
    """Find a y for horizontal routing that avoids intermediate nodes."""
    lo_x, hi_x = min(x1, x2), max(x1, x2)
    blocked: list[tuple[float, float]] = []
    for nx, ny, nw_r, nh in node_rects:
        if nx + nw_r > lo_x + 1 and nx < hi_x - 1:
            if nx > lo_x + 1 and nx + nw_r < hi_x - 1:
                blocked.append((ny - margin, ny + nh + margin))

    if not blocked:
        return mid_y

    def is_clear(y: float) -> bool:
        return all(not (lo <= y <= hi) for lo, hi in blocked)

    if is_clear(mid_y):
        return mid_y

    for offset in range(1, 200):
        step = offset * 5
        if is_clear(mid_y + step):
            return mid_y + step
        if is_clear(mid_y - step):
            return mid_y - step

    return mid_y


def _edge_path(
    x1: float, y1: float, x2: float, y2: float,
    node_rects: list[tuple[float, float, float, float]],
    tx: "Callable[[float], float]",
    ty: "Callable[[float], float]",
    ts: "Callable[[float], float]",
    nw: float,
    src_x_offset: float = 0,
    tgt_x_offset: float = 0,
) -> str:
    """Generate SVG path: straight for same-column, orthogonal for cross.

    After routing, appends a thin virtual rect for the horizontal segment
    to node_rects so subsequent edges avoid the same channel.
    """
    sx1 = tx(x1) + nw / 2 + src_x_offset
    sy1 = ty(y1)
    sx2 = tx(x2) + nw / 2 + tgt_x_offset
    sy2 = ty(y2)
    r = ts(BEND_RADIUS) if ts(BEND_RADIUS) > 2 else 3
    # Minimum final segment so the arrow doesn't hide the rounded corner
    min_final = r + 14
    # Thickness of virtual rect claimed by each horizontal routing channel
    chan_h = 6

    if abs(sx1 - sx2) < 1:
        return f"M{sx1},{sy1} L{sx2},{sy2}"

    dx = sx2 - sx1
    sign_x = 1 if dx > 0 else -1

    def _claim_channel(mid_y: float) -> None:
        """Add a thin virtual rect so future edges avoid this y-level."""
        lo_x = min(sx1, sx2)
        node_rects.append((lo_x, mid_y - chan_h / 2,
                           abs(dx), chan_h))

    def _first_block_top(vx: float, vy_lo: float, vy_hi: float) -> float:
        """Return top y of the first node blocking a vertical at vx, or -1."""
        best = -1.0
        for rx, ry, rw, rh in node_rects:
            if (rx < vx + 2 and rx + rw > vx - 2
                    and ry + rh > vy_lo + 2 and ry < vy_hi - 2):
                if best < 0 or ry < best:
                    best = ry
        return best

    # Minimum clearance from source/target for visible rounded turns
    min_turn = r + 16

    def _blocked_route(block_top: float) -> str:
        """Route around a vertical blocker: horizontal just above it."""
        exit_y = block_top - ts(8)
        if exit_y < sy1 + ts(8):
            exit_y = sy1 + ts(8)
        exit_y = _find_clear_y(exit_y, sx1, sx2, node_rects)
        _claim_channel(exit_y)
        points = [
            (sx1, sy1),
            (sx1, exit_y),
            (sx2, exit_y),
            (sx2, sy2),
        ]
        return _polyline_path(points, r)

    if sy2 >= sy1:
        raw_mid = (sy1 + sy2) / 2
        # Enforce minimum distance from both source and target
        min_mid = sy1 + min_turn + r
        max_mid = sy2 - min_final - r
        clamped = max(min(raw_mid, max_mid), min_mid)
        mid_y = _find_clear_y(clamped, sx1, sx2, node_rects)
        mid_y = max(min(mid_y, max_mid), min_mid)
        _claim_channel(mid_y)

        # Check if the initial vertical from source would pass through a node
        block_top = _first_block_top(sx1, sy1, mid_y)
        if block_top > 0:
            return _blocked_route(block_top)

        half_top = abs(mid_y - sy1)
        half_bot = abs(sy2 - mid_y)
        half_dx = abs(dx) / 2
        ar = min(r, half_top - 1, half_bot - 1, half_dx - 1)
        if ar < 1:
            return (f"M{sx1},{sy1} L{sx1},{mid_y} "
                    f"L{sx2},{mid_y} L{sx2},{sy2}")
        return (
            f"M{sx1},{sy1} L{sx1},{mid_y - ar} "
            f"Q{sx1},{mid_y} {sx1 + ar * sign_x},{mid_y} "
            f"L{sx2 - ar * sign_x},{mid_y} "
            f"Q{sx2},{mid_y} {sx2},{mid_y + ar} "
            f"L{sx2},{sy2}"
        )
    else:
        below_y = sy1 + min_turn
        mid_y = _find_clear_y(below_y, sx1, sx2, node_rects)
        _claim_channel(mid_y)

        # Check if the initial vertical from source would pass through a node
        block_top = _first_block_top(sx1, sy1, mid_y)
        if block_top > 0:
            return _blocked_route(block_top)

        ar = min(r, abs(dx) / 2 - 1, max(1, abs(mid_y - sy1) - 1))
        sign_y = 1 if sy2 > mid_y else -1
        if ar < 1:
            return (f"M{sx1},{sy1} L{sx1},{mid_y} "
                    f"L{sx2},{mid_y} L{sx2},{sy2}")
        return (
            f"M{sx1},{sy1} L{sx1},{mid_y - ar} "
            f"Q{sx1},{mid_y} {sx1 + ar * sign_x},{mid_y} "
            f"L{sx2 - ar * sign_x},{mid_y} "
            f"Q{sx2},{mid_y} {sx2},{mid_y + ar * sign_y} "
            f"L{sx2},{sy2}"
        )


def generate_svg(
    flow_name: str,
    flow: dict,
    positions: dict[str, dict],
    node_section: dict[str, int],
    output_path: Path,
) -> None:
    """Write an SVG overview of the flow to output_path."""
    nodes_by_id = {n["id"]: n for n in flow["flowPlugins"]}
    edges       = flow["flowEdges"]
    pad         = 80

    all_x = [p["x"] for p in positions.values()]
    all_y = [p["y"] for p in positions.values()]
    min_x, min_y = min(all_x), min(all_y)
    max_x = max(all_x) + NODE_WIDTH
    max_y = (max(all_y)
             + max(estimate_height(n) for n in flow["flowPlugins"]))

    coord_w = max_x - min_x + 2 * pad
    coord_h = max_y - min_y + 2 * pad

    scale = SVG_DISPLAY_WIDTH / coord_w
    svg_w = SVG_DISPLAY_WIDTH
    svg_h = max(1, round(coord_h * scale))
    nw    = round(NODE_WIDTH * scale)
    font  = max(7, round(nw / 20))  # scale font to node width

    def tx(cx): return round((cx - min_x + pad) * scale, 1)
    def ty(cy): return round((cy - min_y + pad) * scale, 1)
    def ts(v):  return round(v * scale, 1)

    out: list[str] = []
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' width="{svg_w}" height="{svg_h}"'
        f' viewBox="0 0 {svg_w} {svg_h}">'
    )
    out.append("<defs>")
    for hk, hc in EDGE_COLORS.items():
        out.append(_svg_marker(hk, hc))
    out.append("</defs>")
    out.append(f'<rect width="{svg_w}" height="{svg_h}" fill="#FAFAFA"/>')
    out.append(
        f'<text x="{svg_w // 2}" y="20" text-anchor="middle"'
        f' font-family="monospace" font-size="14"'
        f' font-weight="bold" fill="#333">'
        f'{html.escape(flow_name)}</text>'
    )

    # ── Build node rects in SVG space for collision detection ──────────
    node_rects_svg: list[tuple[float, float, float, float]] = []
    for node in flow["flowPlugins"]:
        nid = node["id"]
        if nid not in positions:
            continue
        pos = positions[nid]
        node_rects_svg.append((
            tx(pos["x"]), ty(pos["y"]),
            nw, ts(estimate_height(node)),
        ))

    # ── Compute section gap centers for cross-section routing ────────
    section_max_x: dict[int, float] = {}
    section_min_x: dict[int, float] = {}
    for nid in positions:
        sec = node_section.get(nid, 0)
        nx_val = tx(positions[nid]["x"])
        right = nx_val + nw
        if sec not in section_max_x or right > section_max_x[sec]:
            section_max_x[sec] = right
        if sec not in section_min_x or nx_val < section_min_x[sec]:
            section_min_x[sec] = nx_val
    sections_sorted = sorted(section_max_x.keys())
    gap_centers: dict[tuple[int, int], float] = {}
    for i in range(len(sections_sorted) - 1):
        s1, s2 = sections_sorted[i], sections_sorted[i + 1]
        cx_gap = (section_max_x[s1] + section_min_x[s2]) / 2
        gap_centers[(s1, s2)] = cx_gap
        gap_centers[(s2, s1)] = cx_gap

    r = ts(BEND_RADIUS) if ts(BEND_RADIUS) > 2 else 3

    # ── Pre-compute fan-out / fan-in offsets ──────────────────────────
    # Spread edge endpoints evenly across the node width
    node_pad = nw * 0.15  # keep edges away from node corners
    usable_w = nw - 2 * node_pad  # usable width for edge endpoints

    edges_by_source: dict[str, list[dict]] = defaultdict(list)
    edges_by_target: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        sid, tid = edge.get("source"), edge.get("target")
        if sid in positions and tid in positions:
            edges_by_source[sid].append(edge)
            edges_by_target[tid].append(edge)

    # Sort outgoing edges by target x position (left-to-right)
    for sid in edges_by_source:
        edges_by_source[sid].sort(
            key=lambda e: positions.get(e["target"], {}).get("x", 0))
    # Sort incoming edges by source x position
    for tid in edges_by_target:
        edges_by_target[tid].sort(
            key=lambda e: positions.get(e["source"], {}).get("x", 0))

    def _source_offset(edge: dict) -> float:
        """Offset from node center, evenly distributed across node width."""
        sid = edge["source"]
        group = edges_by_source[sid]
        n = len(group)
        if n <= 1:
            return 0
        i = group.index(edge)
        # Distribute from -usable_w/2 to +usable_w/2
        return -usable_w / 2 + usable_w * i / (n - 1)

    def _target_offset(edge: dict) -> float:
        """Offset from node center, evenly distributed across node width."""
        tid = edge["target"]
        group = edges_by_target[tid]
        n = len(group)
        if n <= 1:
            return 0
        i = group.index(edge)
        return -usable_w / 2 + usable_w * i / (n - 1)

    # Pre-compute gap offsets for cross-section edges through same gap
    cross_by_gap: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for edge in edges:
        sid, tid = edge.get("source"), edge.get("target")
        if sid not in positions or tid not in positions:
            continue
        s_sec = node_section.get(sid, 0)
        t_sec = node_section.get(tid, 0)
        if s_sec != t_sec:
            gap_key = (min(s_sec, t_sec), min(s_sec, t_sec) + 1)
            cross_by_gap[gap_key].append(edge)

    gap_edge_offset: dict[str, float] = {}
    for gap_key, gap_edges in cross_by_gap.items():
        n = len(gap_edges)
        gap_spread = min(ts(SECTION_GAP) * 0.5, n * ts(12))
        for i, e in enumerate(gap_edges):
            gap_edge_offset[e["id"]] = (
                (i - (n - 1) / 2) * gap_spread / max(n - 1, 1)
            )

    # ── Draw all edges ────────────────────────────────────────────────
    for edge in edges:
        sid = edge.get("source")
        tid = edge.get("target")
        if sid not in positions or tid not in positions:
            continue

        s_sec = node_section.get(sid, 0)
        t_sec = node_section.get(tid, 0)

        sp = positions[sid]
        tp = positions[tid]
        src_h = estimate_height(nodes_by_id.get(sid, {}))

        handle = edge.get("sourceHandle", "1")
        if handle == "2":
            marker_key = "2"
        elif handle == "err1":
            marker_key = "err1"
        else:
            marker_key = "1"
        mid = {"1": "ok", "2": "no", "err1": "err"}[marker_key]
        color = EDGE_COLORS[marker_key]

        s_off = _source_offset(edge)
        t_off = _target_offset(edge)
        sx1 = tx(sp["x"]) + nw / 2 + s_off
        sy1 = ty(sp["y"]) + ts(src_h)
        sx2 = tx(tp["x"]) + nw / 2 + t_off
        sy2 = ty(tp["y"])

        if s_sec != t_sec:
            # Cross-section: route through the gap between sections
            # Offset exit/entry margins so parallel cross-section edges
            # from the same source or to the same target don't overlap
            base_margin = ts(25)
            cross_src = [e for e in edges_by_source[sid]
                         if node_section.get(e["target"], 0) != s_sec]
            cross_tgt = [e for e in edges_by_target[tid]
                         if node_section.get(e["source"], 0) != t_sec]
            src_idx = cross_src.index(edge) if edge in cross_src else 0
            tgt_idx = cross_tgt.index(edge) if edge in cross_tgt else 0
            exit_margin = base_margin + src_idx * ts(12)
            entry_margin = base_margin + tgt_idx * ts(12)

            if s_sec < t_sec:
                base_gap = gap_centers.get(
                    (s_sec, s_sec + 1), (sx1 + sx2) / 2)
            else:
                base_gap = gap_centers.get(
                    (s_sec, s_sec - 1), (sx1 + sx2) / 2)
            gap_x = base_gap + gap_edge_offset.get(edge["id"], 0)
            points = [
                (sx1, sy1),
                (sx1, sy1 + exit_margin),
                (gap_x, sy1 + exit_margin),
                (gap_x, sy2 - entry_margin),
                (sx2, sy2 - entry_margin),
                (sx2, sy2),
            ]
            # Remove zero-length segments
            simplified = [points[0]]
            for p in points[1:]:
                if (abs(p[0] - simplified[-1][0]) > 0.5
                        or abs(p[1] - simplified[-1][1]) > 0.5):
                    simplified.append(p)
            path_d = _polyline_path(simplified, r)
            dash = ' stroke-dasharray="6,3"'
        else:
            # Within-section: orthogonal routed edge
            # Check if this same-column edge will need a horizontal detour
            # (i.e., there are blocking nodes between source and target)
            same_col = abs(sp["x"] - tp["x"]) < 1
            needs_detour = False
            if same_col:
                # Check if any node rect blocks the straight path
                cx_check = tx(sp["x"]) + nw / 2
                y_lo = min(sy1, sy2)
                y_hi = max(sy1, sy2)
                for rx, ry, rw, rh in node_rects_svg:
                    if (rx < cx_check + nw / 2 and rx + rw > cx_check - nw / 2
                            and ry + rh > y_lo + 2 and ry < y_hi - 2):
                        needs_detour = True
                        break
            if same_col and not needs_detour:
                # Straight vertical — use offsets only when multi-edge
                path_d = _edge_path(
                    sp["x"], sp["y"] + src_h, tp["x"], tp["y"],
                    node_rects_svg, tx, ty, ts, nw,
                    src_x_offset=s_off, tgt_x_offset=t_off,
                )
            else:
                # Cross-column or same-column detour: full offset routing
                path_d = _edge_path(
                    sp["x"], sp["y"] + src_h, tp["x"], tp["y"],
                    node_rects_svg, tx, ty, ts, nw,
                    src_x_offset=s_off, tgt_x_offset=t_off,
                )
            dash = ""

        out.append(
            f'<path d="{path_d}" fill="none"'
            f' stroke="{color}" stroke-width="1.5"'
            f' opacity="0.7"{dash}'
            f' marker-end="url(#arr-{mid})"/>'
        )

    # ── Nodes ──────────────────────────────────────────────────────────
    # Approximate max chars that fit in a node at current scale
    char_w = font * 0.6
    max_chars = max(5, int((nw - 10) / char_w))

    for node in flow["flowPlugins"]:
        nid = node["id"]
        if nid not in positions:
            continue
        pos = positions[nid]
        nx, ny = tx(pos["x"]), ty(pos["y"])
        nh = ts(estimate_height(node))
        plugin = node.get("pluginName", "")
        fill = PLUGIN_COLORS.get(plugin, DEFAULT_COLOR)
        tfill = "#FFFFFF" if plugin in LIGHT_TEXT_PLUGINS else "#212121"

        out.append(
            f'<rect x="{nx}" y="{ny}" width="{nw}" height="{nh}"'
            f' rx="5" fill="{fill}" stroke="#9E9E9E"'
            f' stroke-width="0.7"/>'
        )
        name_lines = node.get("name", "").split("\n")
        line_h = font + 2
        total_text = len(name_lines) * line_h
        text_y = ny + (nh - total_text) / 2 + font
        cx = nx + nw / 2
        tspans = "".join(
            f'<tspan x="{cx}" dy="{0 if i == 0 else line_h}">'
            f'{html.escape(line[:max_chars - 1] + "…" if len(line) > max_chars else line)}</tspan>'
            for i, line in enumerate(name_lines)
        )
        out.append(
            f'<text x="{cx}" y="{text_y}" text-anchor="middle"'
            f' font-family="monospace" font-size="{font}"'
            f' fill="{tfill}">{tspans}</text>'
        )

    # ── Plugin color legend ────────────────────────────────────────────
    legend_items = [
        ("#90CAF9", "Input"),     ("#CE93D8", "Config"),
        ("#FFB74D", "Check"),     ("#A5D6A7", "FFmpeg cmd"),
        ("#66BB6A", "Encode"),    ("#2E7D32", "Execute"),
        ("#FFF176", "Validate"),  ("#EF5350", "Replace"),
        ("#FF8A65", "Review"),    ("#4FC3F7", "Notify"),
        ("#B0BEC5", "Error"),     ("#E8EAF6", "Comment"),
    ]
    lx = 10
    ly_top = svg_h - len(legend_items) * 15 - 8
    box_s = 11
    for i, (color, label) in enumerate(legend_items):
        ly = ly_top + i * 15
        out.append(
            f'<rect x="{lx}" y="{ly}" width="{box_s}"'
            f' height="{box_s}" rx="2" fill="{color}"'
            f' stroke="#9E9E9E" stroke-width="0.5"/>'
        )
        out.append(
            f'<text x="{lx + box_s + 5}" y="{ly + box_s - 1}"'
            f' font-family="monospace" font-size="10"'
            f' fill="#444">{html.escape(label)}</text>'
        )

    # Edge color legend
    edge_legend = [
        ("#66BB6A", "Yes / Pass"),
        ("#EF5350", "No / Fail"),
        ("#90A4AE", "Error"),
        ("#66BB6A", "── Cross-section"),
    ]
    ex = 10
    ey_top = ly_top - len(edge_legend) * 15 - 10
    for i, (color, label) in enumerate(edge_legend):
        ey = ey_top + i * 15
        is_dashed = "Cross-section" in label
        dash_attr = ' stroke-dasharray="6,3"' if is_dashed else ""
        out.append(
            f'<line x1="{ex}" y1="{ey + 5}"'
            f' x2="{ex + 20}" y2="{ey + 5}"'
            f' stroke="{color}" stroke-width="2"'
            f'{dash_attr}/>'
        )
        out.append(
            f'<text x="{ex + 24}" y="{ey + box_s - 1}"'
            f' font-family="monospace" font-size="10"'
            f' fill="#444">{html.escape(label)}</text>'
        )

    out.append("</svg>")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(out))
    print(f"    SVG → {output_path.name}  ({svg_w}×{svg_h} px)")


# ── Save + layout + generate ───────────────────────────────────────────────────

def apply_and_save(
    flow_path: Path,
    col_map: dict[str, int],
    num_sections: int = 3,
) -> None:
    with open(flow_path, encoding="utf-8") as f:
        flow = json.load(f)

    positions, topo, _out_adj = compute_positions(flow, col_map)

    # Find break points and split into sections
    break_ys = _find_break_points(
        positions, flow["flowEdges"], col_map, num_sections,
    )

    node_section = _assign_sections(
        positions, break_ys, col_map, topo, flow["flowEdges"],
    )

    section_width = _compute_section_width(col_map)

    if break_ys:
        positions = apply_wrapping(
            positions, node_section, break_ys, section_width,
        )

    # Update flow JSON positions
    for node in flow["flowPlugins"]:
        if node["id"] in positions:
            node["position"] = positions[node["id"]]

    with open(flow_path, "w", encoding="utf-8") as f:
        json.dump(flow, f, indent=2, ensure_ascii=False)
        f.write("\n")

    all_xs = [p["x"] for p in positions.values()]
    all_ys = [p["y"] for p in positions.values()]
    w = max(all_xs) - min(all_xs) + NODE_WIDTH
    h = max(all_ys) - min(all_ys)
    ratio = w / h if h else 0
    n_sec = len(break_ys) + 1
    print(
        f"  {flow_path.name}: {len(positions)} nodes  "
        f"{w:.0f}×{h:.0f} px  ({n_sec} sections, {ratio:.2f}:1)"
    )

    flow_name = flow.get("name", flow_path.stem)
    generate_svg(
        flow_name, flow, positions, node_section,
        IMAGES_DIR / f"{flow_path.stem}.svg",
    )


# ── Column maps ────────────────────────────────────────────────────────────────
#
# Column x values (500px spacing):
#   VR = -1000  VR branch (far left)
#   L  =  -500  left branches (skip / shared fail-review)
#   M  =     0  main chain
#   R  =   500  right branches (commentary / plex notify)
#   SW =  1000  software libx265 fallback
#
# Shared columns at different y regions:
#   SD = -500   SD/720p encoder (same x as L)
#   HI =  500   4K/1440p encoder (same x as R)
#   E  = -1000  error handler (same x as VR, top of flow)

M, L, R = 0, -500, 500

VR_01 = -1000

VR_RETAG = -500  # retag shortcut column (between M and VR_01)

VR_NODES_01 = {
    "chk_vr": M,
    # ── VR retag guards ──────────────────────────────────────────────
    "grd_vr_ismp4": M, "grd_vr_ishevc": M,
    "grd_vr_unwanted_exact": M, "grd_vr_unwanted_partial": M,
    "grd_vr_hasaac": M,
    # ── VR retag shortcut pipeline ───────────────────────────────────
    "cmt_vr_retag": VR_RETAG, "ffs_vr_retag": VR_RETAG,
    "cmd_vr_retag_mp4": VR_RETAG, "cmd_vr_retag_rmdata": VR_RETAG, "cmd_vr_retag_enc": VR_RETAG,
    "cmd_vr_retag_tags": VR_RETAG, "ffe_vr_retag": VR_RETAG,
    # ── VR full pipeline ─────────────────────────────────────────────
    "cmt_vr": VR_01, "ffs_vr": VR_01, "cmd_vr_loglevel": VR_01,
    "cmd_vr_mp4": VR_01, "cmd_vr_rmsub": VR_01, "cmd_vr_rmimages": VR_01,
    "cmd_vr_hevc": VR_01,
    "grd_vr_codec": VR_01,
    "chk_vr_br": VR_01, "cmd_vr_cap_low": VR_01 - 500, "cmd_vr_cap_high": VR_01 + 500,
    "cmd_vr_tags": VR_01,
    "grd_vr_has_eng": VR_01, "cmd_vr_aac_eng": VR_01, "grd_vr_dup_und": VR_01 - 500,
    "cmd_vr_aac_und": VR_01 + 500,
    "grd_vr_fb_eng": VR_01 - 500, "cmd_vr_ens_fb": VR_01 - 500,
    "cmd_vr_rmaudio": VR_01, "cmd_vr_reorder": VR_01,
    "cmd_vr_nochapters": VR_01, "cmd_vr_rmdata": VR_01, "ffe_vr": VR_01,
    "cmt_vr_reorder2": VR_01, "ffs_vr_reorder": VR_01,
    "cmd_vr_rmdata2": VR_01, "cmd_vr_reorder2": VR_01,
    "cmd_vr_faststart2": VR_01, "ffe_vr_reorder": VR_01,
    "fl_vr_size": VR_01, "fl_vr_dur": VR_01,
    "grd_vr_has_video": VR_01, "grd_vr_has_audio": VR_01,
    "fl_vr_replace": VR_01, "chk_auto_accept_vr": VR_01,
}


def col_map_01() -> dict[str, int]:
    sd, hi, sw = -500, 500, 1000
    e = -1000
    return {
        # ── Config ────────────────────────────────────────────────────
        "inp_001": M, "cmt_config": M,
        "var_rm_commentary": M, "var_enable_plex": M,
        "var_enable_arr": M, "var_auto_accept": M,
        # ── Guards ────────────────────────────────────────────────────
        "cmt_guards": M,
        "grd_ext": M, "grd_vid": M, "grd_aud": M, "grd_ch": M,
        "grd_surr_ch": M, "grd_has_eac3": R,
        "grd_dovi": M, "grd_dovi_non_mp4": R, "grd_tag": M,
        "grd_unwanted_exact": M, "grd_unwanted_partial": M,
        "cmt_optimal": L, "fl_noop": L,
        "cmt_proc": R,
        # ── Low-bitrate skip ──────────────────────────────────────────
        "cmt_lowbit": M, "chk_lowbit": M,
        "cmt_skip_lowbit": L, "fl_skip_lowbit": L,
        # ── Health check ──────────────────────────────────────────────
        "cmt_health": M, "chk_health": M,
        "grd_has_muxincompat": M, "grd_has_safe_audio": R,
        # ── FFmpeg pipeline ───────────────────────────────────────────
        "ffs_001": M, "cmd_loglevel": M,
        "cmt_mp4": M, "cmd_mp4": M,
        "cmt_subs": M, "cmd_rmsub": M,
        "cmt_commentary": M, "chk_rm_commentary": M,
        "cmd_rmcommentary": R,
        "cmt_data": M, "cmd_rmdata": M, "cmd_rmimages": M, "cmd_rmattach": M,
        # ── NVENC / resolution / encoders ─────────────────────────────
        "cmt_nvenc": M, "grd_is_mkv": M, "grd_mkv_hevc": M, "cmd_hevc_force": R,
        "grd_av1": M, "chk_nvenc": M,
        "cmt_resolution": M, "chk_resolution": M,
        "cmd_hevc_sd": sd, "cmd_hevc_1080": M, "cmd_hevc_4k": hi,
        "cmt_sw": sw, "cmd_hevc_sw": sw,
        "chk_br_vlow": M, "chk_br_low": M, "chk_br_mid": M,
        "cmd_cap_vlow": L, "cmd_cap_low": L, "cmd_cap_mid": L, "cmd_cap_high": L,
        "cmt_tags": M, "cmd_tags": M,
        # ── Audio ─────────────────────────────────────────────────────
        "cmt_eac3": M, "grd_eac3_ch": M, "grd_eac3_ch8": L,
        "grd_eac3_has_eng": M, "cmd_eac3_eng": M, "cmd_eac3_fb": R,
        "cmt_audio": M, "grd_has_eng": M, "cmd_ens_eng": M,
        "grd_dup_und": L, "cmd_ens_und": R,
        "grd_fb_eng": L, "cmd_ens_fb": L,
        "cmt_reorder": M, "cmd_reorder": M,
        "cmt_rmmux": M, "cmd_rmmux": M,
        "cmt_exec": M, "cmd_nochapters": M, "ffe_001": M,
        # ── Pass 2: AAC creation ─────────────────────────────────────────
        "chk_health_002": M, "fail_health2": M, "ffs_002": M,
        # ── Pass 2→3 boundary: execute AAC, rescan ───────────────────
        "ffe_aac": M, "ffs_003": M,
        # ── Pass 3: cleanup — strip data → AAC guard → rm audio → rm AC3/MP3 → reorder → faststart
        "cmd_rmdata_003": M, "grd_p3_has_aac": M, "cmt_rmaudio": R, "cmd_rmaudio": R, "cmd_rm_ac3": R, "cmd_rm_mp3": R, "cmd_reorder_002": M, "cmd_faststart2": M, "ffe_002": M,
        # ── Validation ────────────────────────────────────────────────
        "cmt_size": M, "fl_size": M,
        "cmt_toobig": L,
        "fl_review": L,
        "cmt_duration": M, "fl_duration": M,
        "cmt_duration_fail": L,
        "grd_has_video": M, "grd_has_audio": M,
        "fail_no_streams": L,
        "chk_auto_accept": M,
        # ── Replace ───────────────────────────────────────────────────
        "cmt_replace": M, "fl_replace": M,
        "fl_manual_review": L,
        # ── Notifications ─────────────────────────────────────────────
        "cmt_notify": M, "chk_plex_notify": M,
        "web_plex": R,
        "chk_arr_notify": M, "arr_notify": M,
        # ── Error handler ─────────────────────────────────────────────
        "err_on": e, "cmt_err_end": e,
        # ── VR branch ─────────────────────────────────────────────────
        **VR_NODES_01,
    }


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Laying out Tdarr flow positions...")
    apply_and_save(
        FLOWS_DIR / "01_hevc_mp4_direct_play.json",
        col_map_01(),
        num_sections=4,
    )
    print("Done.")
