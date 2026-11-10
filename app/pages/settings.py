"""Settings: config.yaml editor (values + their inline comments, line-preserving save with a backup), your data
folder (backups, restore, bring in an old GridSim folder, recover a lost week), data refresh with per-source
freshness, notification test, and the recommended Windows schedule."""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import streamlit as st

import common
import helpers
from gridsim import userdata
from gridsim.config import DEFAULT_CONFIG_PATH, REPO_ROOT, data_dir, load_config

hr = common.hr
ss = st.session_state
s = common.settings()
cfg = common.cfg()
# GRIDSIM_GUI_CONFIG lets tests point the editor at a scratch copy; normally this is the repo's config.yaml.
CFG_PATH = Path(os.environ.get("GRIDSIM_GUI_CONFIG") or DEFAULT_CONFIG_PATH)

common.page_header("Settings", "Model configuration, data, notifications and the recommended schedule.")
tab_cfg, tab_mine, tab_data, tab_note, tab_sched = st.tabs(["Config", "Your data", "Data", "Notifications", "Schedule"])

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
                userdata.auto_backup(cfg, "before-reset")
                userdata.note_settings_reset()
                LOCAL_CONFIG_PATH.with_name(LOCAL_CONFIG_PATH.name + ".bak").write_bytes(LOCAL_CONFIG_PATH.read_bytes())
                LOCAL_CONFIG_PATH.unlink()
                ss["cfg_saved"] = "All settings are back to the defaults."
                st.rerun()

# ======================================================================================================
with tab_mine:
    def _open(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))  # type: ignore[attr-defined]  # Windows only (button hidden elsewhere)

    stt = userdata.status(cfg)
    if ss.get("ud_msg"):
        kind, msg = ss.pop("ud_msg")
        (st.success if kind == "ok" else st.error)(msg)
    common.callout("Everything GridSim can't download again — locked-in predictions, the Kalshi record and price "
                   "history, your lineup changes, tuning and check-ups, your settings — lives in <b>your data "
                   "folder</b>, outside the GridSim program folder. Updating, re-installing or deleting the program "
                   "folder never touches it, and it is backed up automatically after <b>Lock in</b>, <b>Grade</b>, "
                   "the weekly run and once a day.", tone="good")
    newest = stt["backups"][0] if stt["backups"] else None
    st.html(common.kv_html([
        ("Data folder", hr.esc(str(stt["data_dir"])), ", ".join(f"{v} {k}" for k, v in stt["counts"].items())),
        ("Backups", hr.esc(str(stt["backup_dir"])),
         (f"{len(stt['backups'])} backups, newest {common.mtime_text(newest)}" if newest else "none yet")
         + (" · OneDrive keeps a copy off this PC" if "onedrive" in str(stt["backup_dir"]).lower() else "")),
    ]))
    b1, b2, b3 = st.columns(3)
    if b1.button("Back up now", type="primary", icon=":material/backup:", width="stretch"):
        try:
            pth = userdata.backup(cfg, "manual")
            ss["ud_msg"] = ("ok", f"Backed up to {pth}")
        except Exception as e:
            ss["ud_msg"] = ("err", f"Backup failed — {type(e).__name__}: {e}")
        ss["_status"] = None
        st.rerun()
    if sys.platform == "win32":
        if b2.button("Open data folder", icon=":material/folder_open:", width="stretch"):
            _open(stt["data_dir"])
        if b3.button("Open backups folder", icon=":material/folder_open:", width="stretch"):
            _open(stt["backup_dir"])

    st.markdown("##### Bring in data from an old GridSim folder")
    st.caption("For an older GridSim folder (or a copy of one) that still has your predictions and records. In "
               "Explorer, hold **Shift**, right-click the folder → **Copy as path**, and paste it here. GridSim "
               "<b>adds</b> what's missing — nothing you have now is overwritten or lost — and backs up first.",
               unsafe_allow_html=True)
    old_dir = st.text_input("Old GridSim folder", key="ud_old", placeholder=r"C:\Users\you\Desktop\GameSim-claude-friendly-turing-wk7xhb")
    if st.button("Bring it in", icon=":material/move_to_inbox:", disabled=not old_dir.strip()):
        try:
            rep = userdata.import_folder(old_dir, cfg)
            ss["ud_msg"] = ("err" if rep.get("failed") else "ok",
                            ("Partly done — " if rep.get("failed") else "Done — ")
                            + userdata.format_report(rep).replace("\n", "  \n"))
        except ValueError as e:
            ss["ud_msg"] = ("err", f"Nothing was changed — {e}")
        except Exception as e:
            ss["ud_msg"] = ("err", f"Stopped with an error ({type(e).__name__}: {e}). Anything already brought in "
                                   "stays; pressing Bring it in again is safe.")
        ss["_status"] = None
        st.rerun()

    st.markdown("##### Restore a backup")
    if stt["backups"]:
        def _blabel(p) -> str:
            p = Path(p)
            info = userdata.backup_info(p)
            c = info.get("counts") or {}
            where = "" if info.get("host") in (None, userdata.host()) else f" · from {info['host']}"
            return (f"{common.mtime_text(p)} · {info.get('reason', '?')} · {c.get('predictions', '?')} predictions, "
                    f"{c.get('signal records', '?')} signal records{where} · {p.stat().st_size / 1e6:.1f} MB")
        lost = set(stt.get("lost_backups") or [])
        opts = sorted(stt["backups"], key=lambda p: (p != stt["restore_candidate"], p not in lost))
        pick = st.selectbox("Backup", opts, format_func=_blabel, key="ud_pick")
        st.caption("Restoring brings back everything in that backup that's missing now. Nothing current is deleted "
                   "or overwritten (your current version is backed up first).")
        if st.button("Restore this backup", icon=":material/settings_backup_restore:"):
            try:
                rep = userdata.restore(pick, cfg)
                ss["ud_msg"] = ("err" if rep.get("failed") else "ok",
                                "Restored — " + userdata.format_report(rep).replace("\n", "  \n"))
            except Exception as e:
                ss["ud_msg"] = ("err", f"Restore failed — {e}. Pressing Restore again is safe.")
            ss["_status"] = None
            st.rerun()
    else:
        st.caption("No backups yet — the first one is made after your next Lock in, Grade or **Back up now**.")

    with st.expander("Where backups go"):
        cur_dir = (cfg.raw.get("backup") or {}).get("dir") or ""
        nd = st.text_input("Backup folder (empty = automatic)", value=cur_dir, key="ud_bdir",
                           help="Automatic = your OneDrive folder\\GridSim Backups when OneDrive is set up (a copy off "
                                "this PC), else your user folder\\GridSim Backups. A USB drive works too, e.g. "
                                "E:\\GridSim Backups.")
        if st.button("Save backup folder") and nd.strip() != cur_dir:
            from gridsim.config import load_local, save_local
            target = userdata.normalize_dir(nd) if nd.strip() else None
            if nd.strip() and target is None:
                st.error("Please type a full folder path, such as E:\\GridSim Backups (with the drive letter).")
            else:
                try:
                    if target is not None:
                        target.mkdir(parents=True, exist_ok=True)
                        probe = target / ".gridsim-write-test"
                        probe.write_text("ok")
                        probe.unlink()
                    loc = load_local()
                    loc.setdefault("backup", {})
                    if target is None:
                        loc["backup"].pop("dir", None)
                    else:
                        loc["backup"]["dir"] = str(target)
                    save_local(loc)
                    userdata.remember_backup_dir(userdata.backup_dir(load_config()))
                    ss["ud_msg"] = ("ok", f"Backups now go to {userdata.backup_dir(load_config())}")
                    st.rerun()
                except OSError as e:
                    st.error(f"GridSim can't write to that folder ({e}). Pick another one.")

    with st.expander("Recover a lost week (after a data loss)"):
        st.caption("Only for weeks whose data is gone and not in any backup. **Kalshi closing prices** come back from "
                   "Kalshi's public price history (marked *backfilled*). **Predictions** are re-simulated using only "
                   "data from before that week and marked *reconstructed*: they show up separately on the "
                   "Scorecard but never count toward your record or the Signals trust — only predictions locked in "
                   "before kickoff do.")
        rc1, rc2 = st.columns(2)
        try:
            _cur_week = int(s["week"] or common.slate(s["season"], None)[1])
        except Exception:
            _cur_week = 3
        r_season = rc1.number_input("Season", 2021, 2100, int(s["season"]), key="ud_rs")
        r_week = rc2.number_input("Week", 2, 22, max(2, _cur_week - 1), key="ud_rw",
                                  help="Predictions can be re-created from week 2 on (week 1 has no earlier week).")
        x1, x2 = st.columns(2)
        if x1.button("Fill in missing Kalshi closing prices", width="stretch"):
            with st.spinner("Reading Kalshi's price history …"):
                try:
                    from gridsim.scanner import backfill_closing_prices
                    rep = backfill_closing_prices(cfg, int(r_season), int(r_week), verbose=False)
                    ss["ud_msg"] = ("ok", f"Filled {rep['filled']} of {rep['needed']} missing closing prices"
                                    + (f" ({rep['no_history']} had no history on Kalshi)." if rep["no_history"] else "."))
                except Exception as e:
                    ss["ud_msg"] = ("err", f"{type(e).__name__}: {e}")
            st.rerun()
        if x2.button("Re-create that week's predictions", width="stretch"):
            with st.status(f"Re-simulating week {int(r_week)} with data from before it …", expanded=True) as stbox:
                log = st.empty()
                try:
                    from gridsim.live import reconstruct_week
                    with common.capture_output(log, keep_lines=6):
                        paths = reconstruct_week(cfg, int(r_season), int(r_week), sims=int(s["sims"]),
                                                 seed=int(s["seed"]), use_players=bool(s.get("players", True)))
                    stbox.update(label=f"{len(paths)} reconstructed predictions", state="complete", expanded=False)
                except Exception as e:
                    stbox.update(label="Failed", state="error")
                    st.error(f"{type(e).__name__}: {e}")

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
            st.warning("No channel reached — none is configured (or all failed, see above). Create a `.env` file in "
                       f"your data folder ({data_dir()}) with `{nc['ntfy_topic_env']}=your-private-topic` (free ntfy app on your phone) "
                       f"and/or `{nc['discord_webhook_env']}=https://discord.com/api/webhooks/…`.")
    st.caption(f"`.env` lives in your data folder: `{data_dir() / '.env'}` (one left in the GridSim program folder by an "
               "older version is moved there automatically). Values are never shown here.")

# ======================================================================================================
with tab_sched:
    try:
        sec = helpers.readme_section((REPO_ROOT / "README.md").read_text(encoding="utf-8"), "## Scheduling")
    except OSError:
        sec = None
    common.callout("<b>Easiest:</b> double-click <b>Schedule GridSim</b> in the GridSim folder and press <b>A</b>. "
                   "It creates three background tasks (Tuesday 9:00 weekly routine, Friday 19:00 lock-in, Kalshi "
                   "price check every 30 minutes, every day). Run it again and press <b>R</b> to remove them. "
                   f"What they did is written to <code>{hr.esc(str(data_dir() / 'logs' / 'scheduled.log'))}</code>.",
                   tone="good")
    with st.expander("Details for console users (README)"):
        st.markdown(sec or helpers.DEFAULT_SCHEDULE)
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
