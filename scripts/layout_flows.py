#!/usr/bin/env python3
"""
Compute clean ReactFlow positions for Tdarr flow JSON files,
then generate SVG overviews for README documentation.

Algorithm:
  1. Topological sort (Kahn's) over the node graph.
  2. For each node, Y = max(column_cursor[x], max_predecessor_bottom + GAP).
  3. Column (X) is determined by a per-flow lookup table that encodes
     the logical role of each node (main chain, left skip branch,
     right error/action branch, etc.).
  4. Heights are estimated from node name line-count + plugin type.
  5. After layout, if the aspect ratio is too tall, the main chain is
     split into multiple horizontal sections for a more rectangular shape.
  6. An SVG overview is written to images/ for README documentation.
"""

import html
import json
from collections import defaultdict, deque
from pathlib import Path

FLOWS_DIR  = Path(__file__).parent.parent / "flows"
IMAGES_DIR = Path(__file__).parent.parent / "images"

GAP               = 40    # vertical gap between nodes (px)
NODE_WIDTH        = 200   # Tdarr node width — used for SVG rendering only
WRAP_TARGET_RATIO = 1.8   # target width:height after wrapping
SVG_DISPLAY_WIDTH = 1600  # SVG rendered width (px)


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
) -> tuple[dict[str, dict], list[str]]:
    """
    Given a flow dict and a {node_id -> x_column} mapping, return
    ({node_id -> {x, y}}, topo_order) using topological-sort + column-cursor layout.
    """
    nodes    = {n["id"]: n for n in flow["flowPlugins"]}
    edges    = flow["flowEdges"]
    out_adj  : dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int]       = {n["id"]: 0 for n in flow["flowPlugins"]}

    for e in edges:
        if e["source"] not in nodes or e["target"] not in nodes:
            print(f"    WARNING: edge '{e.get('id')}' references unknown node(s), skipping")
            continue
        out_adj[e["source"]].append(e["target"])
        in_degree[e["target"]] += 1

    queue: deque[str] = deque(
        n["id"] for n in flow["flowPlugins"] if in_degree[n["id"]] == 0
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
        print(f"    WARNING: cycle detected — {len(missing)} node(s) excluded from layout: {missing}")

    col_cursor: dict[int, int] = defaultdict(int)
    min_y:      dict[str, int] = defaultdict(int)
    positions:  dict[str, dict] = {}

    for nid in topo:
        node = nodes.get(nid)
        if node is None:
            continue
        x = col_map.get(nid, 0)
        y = max(col_cursor[x], min_y[nid])
        y = (y + 9) // 10 * 10          # snap to 10 px grid

        h = estimate_height(node)
        positions[nid] = {"x": x, "y": y}
        col_cursor[x]  = max(col_cursor[x], y + h + GAP)

        bottom = y + h
        for target in out_adj[nid]:
            min_y[target] = max(min_y[target], bottom + GAP)

    return positions, topo


# ── Horizontal wrapping ────────────────────────────────────────────────────────

def compute_section_x_shift(col_map: dict[str, int]) -> int:
    """Width of one section: column span + inter-section gap."""
    vals = list(col_map.values())
    return max(vals) - min(vals) + 500


def compute_num_sections(
    total_height: int, section_x_shift: int, target_ratio: float
) -> int:
    """Find the number of sections N that minimises |actual_ratio - target_ratio|."""
    best_n, best_diff = 1, float("inf")
    for n in range(1, 8):
        width = n * section_x_shift
        ratio = width / (total_height / n) if total_height else 0
        diff  = abs(ratio - target_ratio)
        if diff < best_diff:
            best_diff, best_n = diff, n
    return best_n


def apply_wrapping(
    positions:       dict[str, dict],
    edges:           list[dict],
    col_map:         dict[str, int],
    topo:            list[str],
    section_x_shift: int,
    num_sections:    int,
) -> dict[str, dict]:
    """Redistribute nodes into multiple horizontal sections for a rectangular layout."""
    if num_sections <= 1:
        return positions

    # Main chain (M=0) nodes sorted by y
    m_nodes_by_y = sorted(
        [(nid, positions[nid]["y"]) for nid in topo
         if nid in positions and col_map.get(nid) == M],
        key=lambda t: t[1],
    )
    if not m_nodes_by_y:
        return positions

    total_height  = m_nodes_by_y[-1][1]
    section_height = total_height / num_sections

    # Assign main-chain nodes to sections by y position
    node_section: dict[str, int] = {}
    for nid, y in m_nodes_by_y:
        node_section[nid] = min(int(y / section_height), num_sections - 1)

    # Build predecessor map
    pred: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        pred[e["target"]].append(e["source"])

    # Propagate sections to branch nodes (topo order ensures predecessors are ready)
    for nid in topo:
        if nid not in node_section:
            sects = [node_section[p] for p in pred[nid] if p in node_section]
            node_section[nid] = max(sects, default=0)

    # Each section's y-start = min y of main-chain nodes assigned to that section
    section_y_starts: dict[int, float] = {s: float("inf") for s in range(num_sections)}
    for nid, y in m_nodes_by_y:
        s = node_section[nid]
        section_y_starts[s] = min(section_y_starts[s], y)
    for s in range(num_sections):
        if section_y_starts[s] == float("inf"):
            section_y_starts[s] = s * section_height

    # Apply x-shift and y-reset per section; clamp y to >= 0
    return {
        nid: {
            "x": pos["x"] + node_section.get(nid, 0) * section_x_shift,
            "y": max(0, pos["y"] - section_y_starts[node_section.get(nid, 0)]),
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
    mid = handle_key.replace("err1", "err").replace("1", "ok").replace("2", "no")
    return (
        f'<marker id="arr-{mid}" markerWidth="6" markerHeight="6"'
        f' refX="5" refY="3" orient="auto">'
        f'<path d="M0,0 L0,6 L6,3 z" fill="{color}"/></marker>'
    )


def generate_svg(
    flow_name:   str,
    flow:        dict,
    positions:   dict[str, dict],
    output_path: Path,
) -> None:
    """Write an SVG overview of the flow to output_path."""
    nodes_by_id = {n["id"]: n for n in flow["flowPlugins"]}
    edges       = flow["flowEdges"]
    PAD         = 50

    # Bounding box (nodes are positioned at top-left)
    all_x = [p["x"] for p in positions.values()]
    all_y = [p["y"] for p in positions.values()]
    min_x = min(all_x)
    min_y = min(all_y)
    max_x = max(all_x) + NODE_WIDTH
    max_y = max(all_y) + max(estimate_height(n) for n in flow["flowPlugins"])

    coord_w = max_x - min_x + 2 * PAD
    coord_h = max_y - min_y + 2 * PAD

    scale = SVG_DISPLAY_WIDTH / coord_w
    svg_w = SVG_DISPLAY_WIDTH
    svg_h = max(1, round(coord_h * scale))
    nw    = round(NODE_WIDTH * scale)
    font  = 11  # display-space px

    def tx(cx: float) -> float: return round((cx - min_x + PAD) * scale, 1)
    def ty(cy: float) -> float: return round((cy - min_y + PAD) * scale, 1)
    def ts(v:  float) -> float: return round(v * scale, 1)

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

    # Background
    out.append(f'<rect width="{svg_w}" height="{svg_h}" fill="#FAFAFA"/>')

    # Title
    out.append(
        f'<text x="{svg_w // 2}" y="18" text-anchor="middle"'
        f' font-family="monospace" font-size="13" font-weight="bold" fill="#333">'
        f'{html.escape(flow_name)}</text>'
    )

    # ── Edges (drawn first, behind nodes) ─────────────────────────────────────
    for edge in edges:
        sid = edge.get("source")
        tid = edge.get("target")
        if sid not in positions or tid not in positions:
            continue
        sp     = positions[sid]
        tp     = positions[tid]
        src_h  = ts(estimate_height(nodes_by_id.get(sid, {})))
        x1     = tx(sp["x"]) + nw / 2
        y1     = ty(sp["y"]) + src_h
        x2     = tx(tp["x"]) + nw / 2
        y2     = ty(tp["y"])
        handle = edge.get("sourceHandle", "1")
        # Map to a defined marker: "2"=no, "err1"=err, anything else=ok
        if handle == "2":
            marker_key = "2"
        elif handle == "err1":
            marker_key = "err1"
        else:
            marker_key = "1"
        mid    = {"1": "ok", "2": "no", "err1": "err"}[marker_key]
        color  = EDGE_COLORS[marker_key]
        cy_c   = (y1 + y2) / 2
        out.append(
            f'<path d="M{x1},{y1} C{x1},{cy_c} {x2},{cy_c} {x2},{y2}"'
            f' fill="none" stroke="{color}" stroke-width="1.5" opacity="0.8"'
            f' marker-end="url(#arr-{mid})"/>'
        )

    # ── Nodes ──────────────────────────────────────────────────────────────────
    for node in flow["flowPlugins"]:
        nid = node["id"]
        if nid not in positions:
            continue
        pos    = positions[nid]
        nx, ny = tx(pos["x"]), ty(pos["y"])
        nh     = ts(estimate_height(node))
        plugin = node.get("pluginName", "")
        fill   = PLUGIN_COLORS.get(plugin, DEFAULT_COLOR)
        tfill  = "#FFFFFF" if plugin in LIGHT_TEXT_PLUGINS else "#212121"

        out.append(
            f'<rect x="{nx}" y="{ny}" width="{nw}" height="{nh}"'
            f' rx="4" fill="{fill}" stroke="#9E9E9E" stroke-width="0.5"/>'
        )
        name_lines  = node.get("name", "").split("\n")
        line_h      = font + 2
        total_text  = len(name_lines) * line_h
        text_y      = ny + (nh - total_text) / 2 + font
        cx          = nx + nw / 2
        tspans      = "".join(
            f'<tspan x="{cx}" dy="{0 if i == 0 else line_h}">'
            f'{html.escape(line)}</tspan>'
            for i, line in enumerate(name_lines)
        )
        out.append(
            f'<text x="{cx}" y="{text_y}" text-anchor="middle"'
            f' font-family="monospace" font-size="{font}" fill="{tfill}">{tspans}</text>'
        )

    # ── Legend ────────────────────────────────────────────────────────────────
    legend_items = [
        ("#90CAF9",  "Input"),
        ("#CE93D8",  "Config"),
        ("#FFB74D",  "Check / Guard"),
        ("#A5D6A7",  "FFmpeg cmd"),
        ("#66BB6A",  "Video encode"),
        ("#2E7D32",  "Execute"),
        ("#FFF176",  "Validate"),
        ("#EF5350",  "Replace"),
        ("#FF8A65",  "Review"),
        ("#4FC3F7",  "Notify"),
        ("#B0BEC5",  "Error"),
        ("#E8EAF6",  "Comment"),
    ]
    lx     = 8
    ly_top = svg_h - len(legend_items) * 14 - 6
    box_s  = 10
    for i, (color, label) in enumerate(legend_items):
        ly = ly_top + i * 14
        out.append(f'<rect x="{lx}" y="{ly}" width="{box_s}" height="{box_s}" rx="2" fill="{color}" stroke="#9E9E9E" stroke-width="0.5"/>')
        out.append(f'<text x="{lx + box_s + 4}" y="{ly + box_s - 1}" font-family="monospace" font-size="9" fill="#444">{html.escape(label)}</text>')

    # Edge color legend
    edge_legend = [("#66BB6A", "Yes / Success"), ("#EF5350", "No / Failure"), ("#90A4AE", "Error")]
    ex = svg_w - 130
    ey_top = svg_h - len(edge_legend) * 14 - 6
    for i, (color, label) in enumerate(edge_legend):
        ey = ey_top + i * 14
        out.append(f'<line x1="{ex}" y1="{ey + 5}" x2="{ex + 18}" y2="{ey + 5}" stroke="{color}" stroke-width="2"/>')
        out.append(f'<text x="{ex + 22}" y="{ey + box_s - 1}" font-family="monospace" font-size="9" fill="#444">{html.escape(label)}</text>')

    out.append("</svg>")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(out))
    print(f"    SVG → {output_path.name}  ({svg_w}×{svg_h} px)")


# ── Save + layout + generate ───────────────────────────────────────────────────

def apply_and_save(flow_path: Path, col_map: dict[str, int]) -> None:
    with open(flow_path, encoding="utf-8") as f:
        flow = json.load(f)

    positions, topo      = compute_positions(flow, col_map)

    # Wrapping
    all_ys           = [p["y"] for p in positions.values()]
    total_height     = max(all_ys) if all_ys else 0
    section_x_shift  = compute_section_x_shift(col_map)
    num_sections     = compute_num_sections(total_height, section_x_shift, WRAP_TARGET_RATIO)

    if num_sections > 1:
        positions = apply_wrapping(
            positions, flow["flowEdges"],
            col_map, topo, section_x_shift, num_sections,
        )

    for node in flow["flowPlugins"]:
        if node["id"] in positions:
            node["position"] = positions[node["id"]]

    with open(flow_path, "w", encoding="utf-8") as f:
        json.dump(flow, f, indent=2, ensure_ascii=False)
        f.write("\n")

    all_xs   = [p["x"] for p in positions.values()]
    all_ys_f = [p["y"] for p in positions.values()]
    w        = max(all_xs) - min(all_xs) + NODE_WIDTH
    h        = max(all_ys_f) - min(all_ys_f)
    ratio    = w / h if h else 0
    print(
        f"  {flow_path.name}: {len(positions)} nodes  "
        f"{w:.0f}×{h:.0f} px  ({num_sections} section{'s' if num_sections > 1 else ''}, {ratio:.2f}:1)"
    )

    flow_name = flow.get("name", flow_path.stem)
    generate_svg(flow_name, flow, positions, IMAGES_DIR / f"{flow_path.stem}.svg")


# ── Column maps ────────────────────────────────────────────────────────────────
#
# Column x values:
#   L  = -750   left branches  (skip / already-optimal paths)
#   M  =    0   main chain
#   R  =  750   right branches (review / optional-action that rejoins)
#   E  = 1500   global error handler
#
# Flow 01 extras:
#   SD =  -500  SD/720p encoder (fan-out from resolution check)
#   HI =   500  4K/1440p encoder
#   SW =  1050  software libx265 fallback

M, L, R, E = 0, -750, 750, 1500

VR_01 = -1500   # VR branch column for flow 01 (left of L)

VR_NODES_01 = {
    "chk_vr": M,        # detection node stays on main chain
    "cmt_vr": VR_01, "ffs_vr": VR_01,
    "cmd_vr_mp4": VR_01, "cmd_vr_rmsub": VR_01,
    "cmd_vr_hevc": VR_01, "cmd_vr_tags": VR_01,
    "cmd_vr_aac_eng": VR_01, "cmd_vr_aac_und": VR_01,
    "cmd_vr_rmaudio": VR_01, "cmd_vr_reorder": VR_01,
    "ffe_vr": VR_01,
    "cmt_vr_reorder2": VR_01, "ffs_vr_reorder": VR_01,
    "cmd_vr_reorder2": VR_01, "ffe_vr_reorder": VR_01,
    "fl_vr_size": VR_01, "fl_vr_dur": VR_01, "fl_vr_replace": VR_01,
    "chk_auto_accept_vr": VR_01,  # auto-accept gate on VR replace path
}


def col_map_01() -> dict[str, int]:
    SD, HI, SW = -500, 500, 1050
    return {
        # ── Config ───────────────────────────────────────────────────────────
        "inp_001": M, "cmt_config": M,
        "var_rm_commentary": M, "var_enable_plex": M, "var_enable_arr": M,
        "var_auto_accept": M,
        # ── Guards ───────────────────────────────────────────────────────────
        "cmt_guards": M,
        "grd_ext": M, "grd_vid": M, "grd_aud": M, "grd_ch": M,
        # Already-optimal skip path → LEFT
        "cmt_optimal": L, "fl_noop": L,
        # Guards NO → processing pipeline (stays MAIN)
        "cmt_proc": M,
        # ── Low-bitrate skip ──────────────────────────────────────────────────
        "cmt_lowbit": M, "chk_lowbit": M,
        "cmt_skip_lowbit": L, "fl_skip_lowbit": L,
        # ── Health check ──────────────────────────────────────────────────────
        "cmt_health": M, "chk_health": M,
        # ── FFmpeg pipeline ───────────────────────────────────────────────────
        "ffs_001": M,
        "cmt_mp4": M,  "cmd_mp4": M,
        "cmt_subs": M, "cmd_rmsub": M,
        # Commentary removal: check at MAIN, action branch → RIGHT (rejoins)
        "cmt_commentary": M, "chk_rm_commentary": M,
        "cmd_rmcommentary": R,
        # Merge point after commentary removal → MAIN
        "cmt_data": M, "cmd_rmdata": M,
        # ── NVENC check ───────────────────────────────────────────────────────
        "cmt_nvenc": M, "chk_nvenc": M,
        # Resolution tier (NVENC YES path)
        "cmt_resolution": M, "chk_resolution": M,
        "cmd_hevc_sd": SD, "cmd_hevc_1080": M, "cmd_hevc_4k": HI,
        # Software fallback (NVENC NO path) → far right
        "cmt_sw": SW, "cmd_hevc_sw": SW,
        # Merge after encoders → MAIN
        "cmt_tags": M, "cmd_tags": M,
        # ── Audio ─────────────────────────────────────────────────────────────
        "cmt_audio": M, "cmd_ens_eng": M, "cmd_ens_und": M,
        "cmt_eac3": M,  "grd_eac3_ch": M, "cmd_eac3_eng": M, "cmd_eac3_und": M,
        "cmt_rmaudio": M, "cmd_rmaudio": M,
        "cmt_reorder": M, "cmd_reorder": M,
        "cmt_exec": M, "ffe_001": M,
        "cmt_reorder2": M, "ffs_reorder": M, "cmd_reorder2": M, "ffe_reorder": M,
        # ── Validation ────────────────────────────────────────────────────────
        "cmt_size": M, "fl_size": M,
        "cmt_toobig": R, "fail_toobig": R,             # fail oversized transcodes
        "fl_review": R,                               # shared review node (duration mismatch)
        "cmt_duration": M, "fl_duration": M,
        "cmt_duration_fail": R,                     # → fl_review (shared)
        "chk_auto_accept": M,                       # auto-accept gate on main replace path
        # ── Replace ───────────────────────────────────────────────────────────
        "cmt_replace": M, "fl_replace": M,
        "fl_manual_review": R,                      # shared manual-review node
        # ── Notifications ─────────────────────────────────────────────────────
        "cmt_notify": M, "chk_plex_notify": M,
        "web_plex": R,
        "chk_arr_notify": M, "arr_notify": M,
        # ── Error handler ─────────────────────────────────────────────────────
        "err_on": E, "err_reset": E, "err_fail": E,
        # ── VR branch ─────────────────────────────────────────────────────────
        **VR_NODES_01,
    }


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Laying out Tdarr flow positions...")
    apply_and_save(FLOWS_DIR / "01_hevc_mp4_direct_play.json", col_map_01())
    print("Done.")
