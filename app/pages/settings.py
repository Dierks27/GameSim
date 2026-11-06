"""Settings: config.yaml editor (values + their inline comments, line-preserving save with a backup), data
refresh with per-source freshness, notification test, and the recommended Windows schedule."""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import streamlit as st

import common
import helpers
from gridsim.config import DEFAULT_CONFIG_PATH, REPO_ROOT, load_config

hr = common.hr
ss = st.session_state
s = common.settings()
cfg = common.cfg()
# GRIDSIM_GUI_CONFIG lets tests point the editor at a scratch copy; normally this is the repo's config.yaml.
CFG_PATH = Path(os.environ.get("GRIDSIM_GUI_CONFIG") or DEFAULT_CONFIG_PATH)

common.page_header("Settings", "Model configuration, data, notifications and the recommended schedule.")
tab_cfg, tab_data, tab_note, tab_sched = st.tabs(["Config", "Data", "Notifications", "Schedule"])

# ======================================================================================================
with tab_cfg:
    from gridsim.config import LOCAL_CONFIG_PATH, load_local, save_local
    try:
        text = CFG_PATH.read_text(encoding="utf-8")
        entries = helpers.parse_config(text)
        cur = load_config()                      # shipped config.yaml + your config.local.yaml
        local = load_local()
    except Exception as e:
        st.error(f"Could not read the settings: {type(e).__name__}: {e}")
        st.stop()

    def _get(d, path):
        for k in path:
            if not isinstance(d, dict) or k not in d:
                return None, False
            d = d[k]
        return d, True

    st.caption(f"Config hash **{cur.hash}**. Your changes are saved to `{LOCAL_CONFIG_PATH.name}` (only the values "
               "you changed), so updating GridSim never wipes them; `config.yaml` keeps the shipped defaults and the "
               "descriptions. Values marked **(yours)** differ from the default. A new hash means cached sims are "
               "recomputed on the next run.")
    if ss.get("cfg_saved"):
        st.success(ss.pop("cfg_saved"))
    sections = list(dict.fromkeys(e["section"] for e in entries))
    with st.form("cfg_form", border=False):
        new_vals, shown = {}, {}
        for sec in sections:
            sec_entries = [e for e in entries if e["section"] == sec]
            head = sec_entries[0] if sec_entries and sec_entries[0]["path"] == (sec,) else None
            n_edit = sum(e["editable"] for e in sec_entries)
            n_mine = sum(1 for e in sec_entries if e["editable"] and _get(local, e["path"])[1])
            with st.expander(f"**{sec}** · {n_edit} editable" + (f" · {n_mine} yours" if n_mine else ""), expanded=False):
                if head and (head["doc"] or head["comment"]):
                    st.caption(" ".join(x for x in (head["doc"], head["comment"]) if x))
                for e in sec_entries:
                    if e["path"] == (sec,):
                        continue
                    name = ".".join(e["path"][1:])
                    desc = " ".join(x for x in (e["doc"], e["comment"]) if x)
                    if e["kind"] == "section":
                        st.markdown(f"**{name}**" + (f" — <span style='color:var(--muted, #898781)'>{hr.esc(desc)}</span>"
                                                     if desc else ""), unsafe_allow_html=True)
                        continue
                    val, has = _get(cur.raw, e["path"])
                    val = val if has else e["value"]
                    mine = _get(local, e["path"])[1]
                    label = name + ("  (yours)" if mine else "")
                    shown[e["path"]] = val
                    c1, c2 = st.columns([2, 3], vertical_alignment="center")
                    wkey = "cfg__" + "__".join(e["path"])
                    with c1:
                        if e["kind"] == "bool":
                            new_vals[e["path"]] = st.toggle(label, value=bool(val), key=wkey)
                        elif e["kind"] == "int":
                            new_vals[e["path"]] = st.number_input(label, value=int(val), step=1, key=wkey)
                        elif e["kind"] == "float":
                            v = float(val)
                            step = 10.0 ** (math.floor(math.log10(abs(v))) - 1) if v else 0.01
                            new_vals[e["path"]] = st.number_input(label, value=v, step=step, format="%g", key=wkey)
                        elif e["kind"] == "str":
                            new_vals[e["path"]] = st.text_input(label, value=str(val), key=wkey)
                        else:
                            st.text_input(label, value=e["raw"], disabled=True, key=wkey,
                                          help="Lists / mappings can't be edited here.")
                    with c2:
                        st.caption((desc or "—") + (f" · default: {e['value']!r}" if mine else ""))
        saved = st.form_submit_button("Save changes", type="primary", icon=":material/save:")
    if saved:
        changes = {p: v for p, v in new_vals.items() if v != shown[p]}
        if not changes:
            st.info("Nothing changed.")
        else:
            before = cur.hash
            defaults = {e["path"]: e["value"] for e in entries}
            for path, v in changes.items():
                node = local
                for k in path[:-1]:
                    node = node.setdefault(k, {})
                if v == defaults.get(path):
                    node.pop(path[-1], None)            # back to the default: drop it from your settings
                else:
                    node[path[-1]] = v
            try:
                save_local(local)
                new = load_config()
            except Exception as e:
                st.error(f"Not saved — {type(e).__name__}: {e}")
            else:
                lst = ", ".join(f"{'.'.join(p)} = {v!r}" for p, v in changes.items())
                ss["cfg_saved"] = f"Saved {len(changes)} change(s): {lst}. Config hash {before} → **{new.hash}**."
                st.rerun()
    if LOCAL_CONFIG_PATH.exists():
        with st.popover("Reset to defaults", icon=":material/restart_alt:"):
            st.markdown("Removes all your changes (a backup is kept as `config.local.yaml.bak`).")
            if st.button("Yes, reset all settings", type="primary"):
                LOCAL_CONFIG_PATH.with_name(LOCAL_CONFIG_PATH.name + ".bak").write_bytes(LOCAL_CONFIG_PATH.read_bytes())
                LOCAL_CONFIG_PATH.unlink()
                ss["cfg_saved"] = "All settings are back to the defaults."
                st.rerun()

# ======================================================================================================
with tab_data:
    season = s["season"]

    @st.cache_data(ttl=900, show_spinner="Checking nflverse release times …")
    def _fresh(season_: int) -> dict:
        from gridsim.availability import source_freshness
        return source_freshness(common.cfg(), season_)

    c1, c2 = st.columns([2, 3], vertical_alignment="center")
    go = c1.button("Refresh data now", type="primary", width="stretch", icon=":material/refresh:")
    c2.caption(f"Downloads the {season} play-by-play and schedules (nflverse), then rosters, depth charts and injury "
               "reports. Takes a minute or two.")
    if go:
        with st.status("Refreshing data …", expanded=True) as status:
            log = st.empty()
            try:
                from gridsim.availability import refresh_availability
                from gridsim.data import refresh_all
                with common.capture_output(log, keep_lines=8):
                    refresh_all(cfg, [season])
                    refresh_availability(cfg, season)
                for f in (common.slate, common.schedule_index, common.team_roster, _fresh):
                    f.clear()
                status.update(label="Data refreshed", state="complete", expanded=False)
            except Exception as e:
                status.update(label="Refresh failed", state="error")
                st.error(f"{type(e).__name__}: {e}")

    st.markdown("##### Sources")
    try:
        fr = _fresh(season)
        st.dataframe([{"source": k, "downloaded": (v.get("downloaded") or "–").replace("T", " "),
                       "nflverse updated": (v.get("nflverse_updated") or "–").replace("T", " ").replace("Z", " UTC")}
                      for k, v in fr.items()], hide_index=True, width="stretch")
    except Exception as e:
        st.warning(f"Freshness check failed ({e}).")
    st.markdown("##### Local cache files")
    cdir = cfg.resolve_path(cfg.data["cache_dir"])
    files = sorted(cdir.glob("*")) if cdir.exists() else []
    st.dataframe([{"file": p.name, "modified": common.mtime_text(p), "size (MB)": round(p.stat().st_size / 1e6, 1)}
                  for p in files if p.is_file()], hide_index=True, width="stretch")
    st.caption(f"Current-season files older than {cfg.data['refresh_hours']} h are re-downloaded automatically on the next "
               "run; completed seasons never go stale.")

# ======================================================================================================
with tab_note:
    from gridsim import notify
    env = notify.load_env()
    nc = cfg.notify
    ntfy_on, discord_on = bool(env.get(nc["ntfy_topic_env"])), bool(env.get(nc["discord_webhook_env"]))
    toast_on = bool(nc.get("windows_toast")) and sys.platform == "win32"
    st.html(common.kv_html([
        ("ntfy push", "configured" if ntfy_on else "<span class='muted'>not set</span>", f"{nc['ntfy_topic_env']} in .env"),
        ("Discord", "configured" if discord_on else "<span class='muted'>not set</span>", f"{nc['discord_webhook_env']} in .env"),
        ("Windows toast", "on" if toast_on else "<span class='muted'>off</span>",
         "this PC" if sys.platform == "win32" else "only on Windows"),
    ]))
    if st.button("Send test notification", icon=":material/notifications:"):
        log = st.empty()
        try:
            with common.capture_output(log):
                sent = notify.send(cfg, "GridSim test", "test notification")
        except Exception as e:
            sent = None
            st.error(f"{type(e).__name__}: {e}")
        if sent:
            st.success("Reached: " + ", ".join(sent))
        elif sent is not None:
            st.warning("No channel reached — none is configured (or all failed, see above). Create a `.env` file next to "
                       f"config.yaml with `{nc['ntfy_topic_env']}=your-private-topic` (free ntfy app on your phone) "
                       f"and/or `{nc['discord_webhook_env']}=https://discord.com/api/webhooks/…`.")
    st.caption(f"`.env` lives at `{REPO_ROOT / '.env'}` and is git-ignored; values are never shown here.")

# ======================================================================================================
with tab_sched:
    try:
        sec = helpers.readme_section((REPO_ROOT / "README.md").read_text(encoding="utf-8"), "## Scheduling")
    except OSError:
        sec = None
    common.callout("<b>Easiest:</b> double-click <b>Schedule GridSim</b> in the GridSim folder and press <b>A</b>. "
                   "It creates three background tasks (Tuesday 9:00 weekly routine, Friday 19:00 lock-in, Kalshi "
                   "price check every 30 minutes on game days). Run it again and press <b>R</b> to remove them. "
                   f"What they did is written to <code>{hr.esc(str(REPO_ROOT / 'logs' / 'scheduled.log'))}</code>.",
                   tone="good")
    with st.expander("Details for console users (README)"):
        st.markdown(sec or helpers.DEFAULT_SCHEDULE)
    if cfg.scanner.get("schedule_note"):
        st.caption(f"config scanner.schedule_note: {cfg.scanner['schedule_note']}")
    st.markdown("##### Last activity")
    rows = []
    for name, what in (("predictions", "frozen predictions / scorecard"), ("signals", "scanner ledger / snapshots"),
                       ("diagnose", "diagnose reports"), ("out/cache", "cached sims"), ("backtests", "backtests"),
                       ("tuning", "tuning runs")):
        nf = helpers.newest_file(cfg.resolve_path(name))
        rows.append({"folder": name, "contains": what, "newest file": nf[0].name if nf else "–",
                     "modified": nf[1].strftime("%Y-%m-%d %H:%M") if nf else "never"})
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("If the scheduled tasks run, these timestamps move: predictions on Tuesday/Friday, signals every 30 "
               "minutes in game weeks.")
