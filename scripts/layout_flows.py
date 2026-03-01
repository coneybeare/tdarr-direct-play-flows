#!/usr/bin/env python3
"""
Compute clean ReactFlow positions for Tdarr flow JSON files.

Algorithm:
  1. Topological sort (Kahn's) over the node graph.
  2. For each node, Y = max(column_cursor[x], max_predecessor_bottom + GAP).
  3. Column (X) is determined by a per-flow lookup table that encodes
     the logical role of each node (main chain, left skip branch,
     right error/action branch, etc.).
  4. Heights are estimated from node name line-count + plugin type.
"""

import json
from pathlib import Path
from collections import defaultdict, deque

FLOWS_DIR = Path(__file__).parent.parent / "flows"
GAP = 40  # vertical gap between nodes (px)


# ── Height estimation ──────────────────────────────────────────────────────────

def estimate_height(node: dict) -> int:
    lines = node.get("name", "").count("\n") + 1
    is_comment = node.get("pluginName") == "comment"
    base = 60 if is_comment else 50
    min_h = 100 if is_comment else 80
    return max(min_h, base + lines * 20)


# ── Core layout engine ─────────────────────────────────────────────────────────

def compute_positions(flow: dict, col_map: dict[str, int]) -> dict[str, dict]:
    """
    Given a flow dict and a {node_id -> x_column} mapping, return
    {node_id -> {x, y}} using topological-sort + column-cursor layout.
    """
    nodes = {n["id"]: n for n in flow["flowPlugins"]}
    edges = flow["flowEdges"]

    out_adj: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {n["id"]: 0 for n in flow["flowPlugins"]}

    for e in edges:
        out_adj[e["source"]].append(e["target"])
        in_degree[e["target"]] += 1

    # Kahn's topological sort (FIFO preserves natural flow order)
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

    col_cursor: dict[int, int] = defaultdict(int)   # x -> next available y
    min_y: dict[str, int] = defaultdict(int)        # node_id -> min y from predecessors
    positions: dict[str, dict] = {}

    for nid in topo:
        node = nodes.get(nid)
        if node is None:
            continue
        x = col_map.get(nid, 0)
        y = max(col_cursor[x], min_y[nid])
        y = (y + 9) // 10 * 10  # snap up to 10 px grid

        h = estimate_height(node)
        positions[nid] = {"x": x, "y": y}
        col_cursor[x] = max(col_cursor[x], y + h + GAP)

        bottom = y + h
        for target in out_adj[nid]:
            min_y[target] = max(min_y[target], bottom + GAP)

    return positions


def apply_and_save(flow_path: Path, col_map: dict[str, int]) -> None:
    with open(flow_path) as f:
        flow = json.load(f)

    positions = compute_positions(flow, col_map)
    for node in flow["flowPlugins"]:
        if node["id"] in positions:
            node["position"] = positions[node["id"]]

    with open(flow_path, "w") as f:
        json.dump(flow, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"  {flow_path.name}: {len(positions)} nodes laid out")


# ── Column maps ────────────────────────────────────────────────────────────────
#
# Column x values used across flows:
#   L  = -750   left branches  (skip / already-optimal paths)
#   M  =    0   main chain
#   R  =  750   right branches (review / optional-action that rejoins)
#   E  = 1500   global error handler
#
# Flow 01 extras:
#   SD =  -500  SD/720p encoder (fan-out from resolution check)
#   HI =   500  4K/1440p encoder
#   SW =  1050  software libx265 fallback
#
# Flow 03 extras (HDR/SDR split):
#   HDR      = -1100   HDR stream-copy path
#   HDR_B    = -1650   HDR side branches (commentary removal, duration fail)
#   SDR      =  1100   SDR full-transcode path
#   SDR_SD   =   500   SDR SD encoder
#   SDR_4K   =  1700   SDR 4K encoder + review nodes
#   SDR_SW   =  2150   SDR software fallback
#   ERR_03   =  2600   error handler for flow 03

M, L, R, E = 0, -750, 750, 1500


def col_map_01() -> dict[str, int]:
    SD, HI, SW = -500, 500, 1050
    return {
        # ── Config ───────────────────────────────────────────────────────────
        "inp_001": M, "cmt_config": M,
        "var_rm_commentary": M, "var_enable_plex": M, "var_enable_arr": M,
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
        # ── Validation ────────────────────────────────────────────────────────
        "cmt_size": M, "fl_size": M,
        "cmt_toobig": R, "fl_review": R,           # shared review node
        "cmt_duration": M, "fl_duration": M,
        "cmt_duration_fail": R,                     # → fl_review (shared)
        # ── Replace ───────────────────────────────────────────────────────────
        "cmt_replace": M, "fl_replace": M,
        # ── Notifications ─────────────────────────────────────────────────────
        "cmt_notify": M, "chk_plex_notify": M,
        "web_plex": R,
        "chk_arr_notify": M, "arr_notify": M,
        # ── Error handler ─────────────────────────────────────────────────────
        "err_on": E, "err_reset": E, "err_fail": E,
    }


def col_map_02() -> dict[str, int]:
    ERR = 1350
    return {
        # ── Config ───────────────────────────────────────────────────────────
        "inp_001": M, "cmt_config": M,
        "var_rm_commentary": M, "var_enable_plex": M, "var_enable_arr": M,
        # ── About + prerequisite guards ──────────────────────────────────────
        "cmt_about": M, "cmt_guards": M,
        "pre_ext": M, "pre_vid": M, "grd_aud": M, "grd_ch": M,
        # Prerequisite skip paths → LEFT
        "cmt_skip_ext": L, "cmt_skip_vid": L,
        # Already-has-stereo-AAC path → LEFT
        "cmt_optimal": L, "fl_noop": L,
        # Guards NO → processing pipeline (stays MAIN)
        "cmt_proc": M,
        # ── Health check ──────────────────────────────────────────────────────
        "cmt_health": M, "chk_health": M,
        "cmt_health_fail": R, "err_health": R,
        # ── FFmpeg pipeline ───────────────────────────────────────────────────
        "ffs_001": M,
        # Commentary removal: check at MAIN, action branch → LEFT (rejoins)
        "cmt_commentary": M, "chk_rm_commentary": M,
        "cmd_rmcommentary": L,
        # Merge + EAC3 → MAIN
        "cmt_eac3": M, "grd_eac3_ch": M, "cmd_eac3_eng": M, "cmd_eac3_und": M,
        # Stereo AAC
        "cmt_ensure": M, "cmd_ens_eng": M, "cmd_ens_und": M,
        # Remove incompatible audio
        "cmt_rmaudio": M, "cmd_rmaudio": M,
        # Reorder + execute
        "cmt_reorder": M, "cmd_reorder": M,
        "cmt_exec": M, "ffe_001": M,
        # ── Duration validation ───────────────────────────────────────────────
        "cmt_duration": M, "fl_duration": M,
        "cmt_duration_fail": R, "fl_duration_review": R,
        # ── Replace ───────────────────────────────────────────────────────────
        "cmt_replace": M, "fl_replace": M,
        # ── Notifications ─────────────────────────────────────────────────────
        "cmt_notify": M, "chk_plex_notify": M,
        "web_plex": R,
        "chk_arr_notify": M, "arr_notify": M,
        # ── Error handler ─────────────────────────────────────────────────────
        "err_on": ERR, "err_reset": ERR, "err_fail": ERR,
    }


def col_map_03() -> dict[str, int]:
    HDR    = -1100
    HDR_B  = -1650
    SDR    =  1100
    SDR_SD =   500
    SDR_4K =  1700
    SDR_SW =  2150
    ERR    =  2600
    return {
        # ── Config ───────────────────────────────────────────────────────────
        "inp_001": M, "cmt_config": M,
        "var_rm_commentary": M, "var_enable_plex": M, "var_enable_arr": M,
        # ── About + guards ────────────────────────────────────────────────────
        "cmt_about": M, "cmt_guards": M,
        "grd_ext": M, "grd_vid": M, "grd_aud": M, "grd_ch": M,
        # Already-optimal skip → LEFT
        "cmt_optimal": L, "fl_noop": L,
        # Guards NO → health check (stays MAIN)
        "cmt_health": M, "chk_health": M,
        "cmt_health_fail": R, "err_health": R,
        # ── HDR detection ─────────────────────────────────────────────────────
        "cmt_hdr_detect": M, "chk_hdr": M,
        # ── HDR stream-copy path (LEFT column) ───────────────────────────────
        "cmt_hdr_path":           HDR,
        "ffs_hdr":                HDR,
        "cmd_hdr_mp4":            HDR,
        "cmd_hdr_rmsub":          HDR,
        "cmd_hdr_rmdata":         HDR,
        "chk_hdr_rm_commentary":  HDR,
        "cmd_hdr_rmcommentary":   HDR_B,   # side branch → rejoins cmd_hdr_tags
        "cmd_hdr_tags":           HDR,
        "grd_hdr_eac3_ch":        HDR,
        "cmd_hdr_eac3_eng":       HDR,
        "cmd_hdr_eac3_und":       HDR,
        "cmd_hdr_ens_eng":        HDR,
        "cmd_hdr_ens_und":        HDR,
        "cmd_hdr_rmaudio":        HDR,
        "cmd_hdr_reorder":        HDR,
        "ffe_hdr":                HDR,
        "fl_hdr_duration":        HDR,
        "cmt_hdr_dur_fail":       HDR_B,   # duration fail review
        "fl_hdr_dur_review":      HDR_B,
        "fl_hdr_replace":         HDR,
        # ── SDR full-transcode path (RIGHT column) ────────────────────────────
        "cmt_sdr_path":           SDR,
        "ffs_sdr":                SDR,
        "cmd_sdr_mp4":            SDR,
        "cmd_sdr_rmsub":          SDR,
        "cmd_sdr_rmdata":         SDR,
        "chk_sdr_rm_commentary":  SDR,
        "cmd_sdr_rmcommentary":   SDR_4K,  # side branch → rejoins cmt_sdr_nvenc
        "cmt_sdr_nvenc":          SDR,
        "chk_nvenc":              SDR,
        # Resolution tier (NVENC YES)
        "cmt_sdr_resolution":     SDR,
        "chk_resolution":         SDR,
        "cmd_hevc_sd":            SDR_SD,
        "cmd_hevc_1080":          SDR,
        "cmd_hevc_4k":            SDR_4K,
        # SW fallback (NVENC NO)
        "cmt_sdr_sw":             SDR_SW,
        "cmd_hevc_sw":            SDR_SW,
        # Merge after encoders
        "cmd_sdr_tags":           SDR,
        "grd_sdr_eac3_ch":        SDR,
        "cmd_sdr_eac3_eng":       SDR,
        "cmd_sdr_eac3_und":       SDR,
        "cmd_sdr_ens_eng":        SDR,
        "cmd_sdr_ens_und":        SDR,
        "cmd_sdr_rmaudio":        SDR,
        "cmd_sdr_reorder":        SDR,
        "ffe_sdr":                SDR,
        "fl_sdr_size":            SDR,
        "cmt_sdr_toobig":         SDR_4K,  # size fail review
        "fl_sdr_review":          SDR_4K,
        "fl_sdr_duration":        SDR,
        "cmt_sdr_dur_fail":       SDR_4K,  # duration fail review
        "fl_sdr_dur_review":      SDR_4K,
        "fl_sdr_replace":         SDR,
        # ── Merged notifications (both paths converge here) ───────────────────
        "cmt_notify":       M, "chk_plex_notify": M,
        "web_plex":         R,
        "chk_arr_notify":   M, "arr_notify": M,
        # ── Error handler ─────────────────────────────────────────────────────
        "err_on": ERR, "err_reset": ERR, "err_fail": ERR,
    }


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Laying out Tdarr flow positions...")
    apply_and_save(FLOWS_DIR / "01_hevc_mp4_direct_play.json",    col_map_01())
    apply_and_save(FLOWS_DIR / "02_audio_stereo_add_only.json",   col_map_02())
    apply_and_save(FLOWS_DIR / "03_hdr_aware_transcode.json",     col_map_03())
    print("Done.")
