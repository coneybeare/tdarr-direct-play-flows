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
  6. Cross-section edges are replaced with labeled connection indicators.
  7. Within-section edges use orthogonal routing with collision avoidance.
  8. An SVG overview is written to images/ for README documentation.
"""

import html
import json
from collections import defaultdict, deque
from pathlib import Path

FLOWS_DIR  = Path(__file__).parent.parent / "flows"
IMAGES_DIR = Path(__file__).parent.parent / "images"

GAP               = 90     # vertical gap between nodes (px)
NODE_WIDTH        = 420    # Tdarr node width
BEND_RADIUS       = 10     # rounded corner radius for orthogonal edges
SECTION_GAP       = 250    # horizontal gap between wrapped sections
SVG_DISPLAY_WIDTH = 4000   # SVG rendered width (px)
CONNECTOR_RADIUS  = 8      # radius for connection indicator circles


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


def _svg_marker(handle_key: str, color: str) -> str:
    mid = (handle_key.replace("err1", "err")
           .replace("1", "ok").replace("2", "no"))
    return (
        f'<marker id="arr-{mid}" markerWidth="8" markerHeight="8"'
        f' refX="7" refY="4" orient="auto">'
        f'<path d="M0,0 L0,8 L8,4 z" fill="{color}"/></marker>'
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
    tx, ty, ts, nw: float,
) -> str:
    """Generate SVG path: straight for same-column, orthogonal for cross."""
    sx1 = tx(x1) + nw / 2
    sy1 = ty(y1)
    sx2 = tx(x2) + nw / 2
    sy2 = ty(y2)
    r = ts(BEND_RADIUS) if ts(BEND_RADIUS) > 2 else 3

    if abs(sx1 - sx2) < 1:
        return f"M{sx1},{sy1} L{sx2},{sy2}"

    dx = sx2 - sx1
    sign_x = 1 if dx > 0 else -1

    if sy2 >= sy1:
        raw_mid = (sy1 + sy2) / 2
        mid_y = _find_clear_y(raw_mid, sx1, sx2, node_rects)
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
        below_y = sy1 + ts(30)
        mid_y = _find_clear_y(below_y, sx1, sx2, node_rects)
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

    # ── Classify edges: within-section vs cross-section ────────────────
    connector_id = 0
    connectors: list[tuple[int, str, str, str, str]] = []

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

        if s_sec != t_sec:
            # Cross-section: draw connection indicators
            connector_id += 1
            label = str(connector_id)
            connectors.append(
                (connector_id, sid, tid, color, label))

            # Source exit indicator (bottom of source node)
            cx_s = tx(sp["x"]) + nw / 2
            cy_s = ty(sp["y"]) + ts(src_h) + ts(15)
            out.append(
                f'<circle cx="{cx_s}" cy="{cy_s}"'
                f' r="{CONNECTOR_RADIUS}" fill="{color}"'
                f' stroke="#fff" stroke-width="1.5"/>'
            )
            out.append(
                f'<text x="{cx_s}" y="{cy_s + 3.5}"'
                f' text-anchor="middle"'
                f' font-family="monospace" font-size="8"'
                f' font-weight="bold" fill="#fff">'
                f'{html.escape(label)}</text>'
            )

            # Target entry indicator (top of target node)
            cx_t = tx(tp["x"]) + nw / 2
            cy_t = ty(tp["y"]) - ts(15)
            out.append(
                f'<circle cx="{cx_t}" cy="{cy_t}"'
                f' r="{CONNECTOR_RADIUS}" fill="{color}"'
                f' stroke="#fff" stroke-width="1.5"/>'
            )
            out.append(
                f'<text x="{cx_t}" y="{cy_t + 3.5}"'
                f' text-anchor="middle"'
                f' font-family="monospace" font-size="8"'
                f' font-weight="bold" fill="#fff">'
                f'{html.escape(label)}</text>'
            )
            continue

        # Within-section: draw orthogonal routed edge
        path_d = _edge_path(
            sp["x"], sp["y"] + src_h, tp["x"], tp["y"],
            node_rects_svg, tx, ty, ts, nw,
        )
        out.append(
            f'<path d="{path_d}" fill="none"'
            f' stroke="{color}" stroke-width="1.5"'
            f' opacity="0.75"'
            f' marker-end="url(#arr-{mid})"/>'
        )

    # ── Nodes ──────────────────────────────────────────────────────────
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
            f'{html.escape(line)}</tspan>'
            for i, line in enumerate(name_lines)
        )
        out.append(
            f'<text x="{cx}" y="{text_y}" text-anchor="middle"'
            f' font-family="monospace" font-size="{font}"'
            f' fill="{tfill}">{tspans}</text>'
        )

    # ── Connection key (cross-section edge legend) ─────────────────────
    # Rendered AFTER svg_h is finalized; placed in the bottom margin
    # below all nodes, centered horizontally.
    connector_lines: list[str] = []
    connector_height = 0
    if connectors:
        conn_font = 9
        conn_line_h = 13
        connector_height = 20 + len(connectors) * conn_line_h + 10
        key_y = svg_h  # start at current bottom
        key_x = svg_w // 2 - 200
        connector_lines.append(
            f'<text x="{key_x}" y="{key_y + 14}"'
            f' font-family="monospace" font-size="{conn_font}"'
            f' font-weight="bold" fill="#555">'
            f'Cross-section connections:</text>'
        )
        for i, (_, sid, tid, color, label) in enumerate(connectors):
            cy_k = key_y + 28 + i * conn_line_h
            src_n = nodes_by_id[sid]["name"].split("\n")[0]
            tgt_n = nodes_by_id[tid]["name"].split("\n")[0]
            if len(src_n) > 22:
                src_n = src_n[:21] + "…"
            if len(tgt_n) > 22:
                tgt_n = tgt_n[:21] + "…"
            connector_lines.append(
                f'<circle cx="{key_x + 6}" cy="{cy_k - 3}"'
                f' r="5" fill="{color}"/>'
            )
            connector_lines.append(
                f'<text x="{key_x + 6}" y="{cy_k}"'
                f' text-anchor="middle"'
                f' font-family="monospace" font-size="7"'
                f' font-weight="bold" fill="#fff">'
                f'{label}</text>'
            )
            connector_lines.append(
                f'<text x="{key_x + 16}" y="{cy_k}"'
                f' font-family="monospace"'
                f' font-size="{conn_font}" fill="#555">'
                f'{html.escape(src_n)} → '
                f'{html.escape(tgt_n)}</text>'
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
    ]
    ex = 10
    ey_top = ly_top - len(edge_legend) * 15 - 10
    for i, (color, label) in enumerate(edge_legend):
        ey = ey_top + i * 15
        out.append(
            f'<line x1="{ex}" y1="{ey + 5}"'
            f' x2="{ex + 20}" y2="{ey + 5}"'
            f' stroke="{color}" stroke-width="2"/>'
        )
        out.append(
            f'<text x="{ex + 24}" y="{ey + box_s - 1}"'
            f' font-family="monospace" font-size="10"'
            f' fill="#444">{html.escape(label)}</text>'
        )

    # Add connector legend and expand SVG if needed
    if connector_lines:
        out.extend(connector_lines)
        svg_h_final = svg_h + connector_height
    else:
        svg_h_final = svg_h

    out.append("</svg>")

    # Patch the SVG header with the final height
    svg_text = "\n".join(out)
    svg_text = svg_text.replace(
        f'height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}"',
        f'height="{svg_h_final}" viewBox="0 0 {svg_w} {svg_h_final}"',
        1,
    )
    # Also patch the background rect
    svg_text = svg_text.replace(
        f'<rect width="{svg_w}" height="{svg_h}" fill="#FAFAFA"/>',
        f'<rect width="{svg_w}" height="{svg_h_final}" fill="#FAFAFA"/>',
        1,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg_text)
    print(f"    SVG → {output_path.name}  ({svg_w}×{svg_h_final} px)")


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

VR_NODES_01 = {
    "chk_vr": M,
    "cmt_vr": VR_01, "ffs_vr": VR_01,
    "cmd_vr_mp4": VR_01, "cmd_vr_rmsub": VR_01,
    "cmd_vr_hevc": VR_01, "cmd_vr_tags": VR_01,
    "cmd_vr_aac_eng": VR_01, "cmd_vr_aac_und": VR_01,
    "cmd_vr_rmaudio": VR_01, "cmd_vr_reorder": VR_01,
    "cmd_vr_nochapters": VR_01, "ffe_vr": VR_01,
    "cmt_vr_reorder2": VR_01, "ffs_vr_reorder": VR_01,
    "cmd_vr_rmdata2": VR_01, "cmd_vr_reorder2": VR_01,
    "ffe_vr_reorder": VR_01,
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
        "grd_dovi": M, "grd_tag": M,
        "cmt_optimal": L, "fl_noop": L,
        "cmt_proc": M,
        # ── Low-bitrate skip ──────────────────────────────────────────
        "cmt_lowbit": M, "chk_lowbit": M,
        "cmt_skip_lowbit": L, "fl_skip_lowbit": L,
        # ── Health check ──────────────────────────────────────────────
        "cmt_health": M, "chk_health": M,
        # ── FFmpeg pipeline ───────────────────────────────────────────
        "ffs_001": M,
        "cmt_mp4": M, "cmd_mp4": M,
        "cmt_subs": M, "cmd_rmsub": M,
        "cmt_commentary": M, "chk_rm_commentary": M,
        "cmd_rmcommentary": R,
        "cmt_data": M, "cmd_rmdata": M,
        # ── NVENC / resolution / encoders ─────────────────────────────
        "cmt_nvenc": M, "chk_nvenc": M,
        "cmt_resolution": M, "chk_resolution": M,
        "cmd_hevc_sd": sd, "cmd_hevc_1080": M, "cmd_hevc_4k": hi,
        "cmt_sw": sw, "cmd_hevc_sw": sw,
        "cmt_tags": M, "cmd_tags": M,
        # ── Audio ─────────────────────────────────────────────────────
        "cmt_rmaudio": M, "cmd_rmaudio": M,
        "cmt_audio": M, "cmd_ens_eng": M, "cmd_ens_und": M,
        "cmt_reorder": M, "cmd_reorder": M,
        "cmt_exec": M, "cmd_nochapters": M, "ffe_001": M,
        # ── Reorder pass ──────────────────────────────────────────────
        "cmt_reorder2": M, "ffs_reorder": M,
        "cmt_eac3": M, "grd_eac3_codec": M, "grd_eac3_ch": M, "grd_eac3_ch8": M,
        "cmd_eac3_eng": M, "cmd_eac3_und": M,
        "cmd_rm_aac": M,
        "cmd_rmdata2": M, "cmd_reorder2": M, "ffe_reorder": M,
        # ── Validation ────────────────────────────────────────────────
        "cmt_size": M, "fl_size": M,
        "cmt_toobig": L, "fail_toobig": L,
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
        "err_on": e, "err_reset": e, "err_fail": e,
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
