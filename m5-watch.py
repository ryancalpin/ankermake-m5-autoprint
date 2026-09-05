#!/usr/bin/env python3
"""m5-watch — AnkerMake M5 print watchdog hook.

Watches the printer's MQTT stream for AI false-alarm pauses & errors while a
print runs unattended. On an AI pause (black-filament-on-black-bed false
positives are common), it captures before/after camera frames and — with
--auto-resume — sends the M5 BREAK_POINT (1039) command to resume, rate-limited
so a genuinely failing print is not fought. Logs to ~/m5-watch.log.

Usage:
  python3 m5-watch.py [--once] [--auto-resume]
    --once         single snapshot then exit (for testing)
    --auto-resume  send BREAK_POINT resume on AI pause (rate-limited)

Requires: ankerctl repo (https://github.com/Ankermgmt/ankermake-m5-protocol)
checked out at $ANKERCTL_DIR with .venv deps and imported auth config.
"""
import sys, time, os, subprocess

ANKERCTL = os.environ.get("ANKERCTL_DIR", os.path.expanduser("~/Documents/3D Printing/ankerctl"))
sys.path.insert(0, ANKERCTL)
sys.path.insert(0, os.path.join(ANKERCTL, "cli"))

import logging
logging.basicConfig(level=logging.CRITICAL)
from cli.config import configmgr
import cli.mqtt as cmqtt
from libflagship.mqtt import MqttMsgType

LOG = os.path.expanduser("~/m5-watch.log")
SHOTS = os.path.expanduser("~/m5-shots")
AUTO_RESUME = "--auto-resume" in sys.argv
ONCE = "--once" in sys.argv
os.makedirs(SHOTS, exist_ok=True)

def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def send(cmd_id, args=None):
    cmd = [f"{ANKERCTL}/.venv/bin/python3", f"{ANKERCTL}/ankerctl.py", "mqtt", "send", str(cmd_id)]
    for k, v in (args or {}).items():
        cmd.append(f"{k}={v}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45,
                           env={**os.environ, "PYTHONPATH": ""}, cwd=ANKERCTL)
        return (r.stdout or r.stderr).strip()[-200:]
    except Exception as e:
        return f"ERR {e}"

def capture_shot(tag):
    """Grab a camera frame during an alarm so the event can be visually verified."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    h264 = os.path.join(SHOTS, f"{tag}-{ts}.h264")
    jpg = os.path.join(SHOTS, f"{tag}-{ts}.jpg")
    try:
        r = subprocess.run(
            [f"{ANKERCTL}/.venv/bin/python3", f"{ANKERCTL}/ankerctl.py", "pppp",
             "capture-video", "-m", "400kb", h264],
            capture_output=True, text=True, timeout=45,
            env={**os.environ, "PYTHONPATH": ""}, cwd=ANKERCTL)
        if r.returncode == 0 and os.path.exists(h264):
            subprocess.run(["ffmpeg", "-y", "-i", h264, "-frames:v", "1", "-update", "1", jpg],
                           capture_output=True, timeout=20)
            if os.path.exists(jpg):
                log(f"shot saved: {jpg}")
                return jpg
    except Exception as e:
        log(f"capture failed: {e}")
    return None

class _Cfg: pass
env = _Cfg()
env.config = configmgr()
env.printer_index = 0
env.insecure = False

def open_client():
    return cmqtt.mqtt_open(env.config, env.printer_index, False)

def main():
    log(f"m5-watch starting (auto_resume={AUTO_RESUME}, once={ONCE})")
    client = open_client()
    log("connected to MQTT")
    last_ai = 0
    ai_resume_count = 0
    last_event_was_pause = False
    stall_start = None
    last_progress = None

    deadline = time.time() + 25 if ONCE else time.time() + 1e12
    while time.time() < deadline:
        try:
            for msg, body in client.fetchloop():
                for obj in body:
                    ct = obj.get("commandType")
                    nm = "?"
                    try:
                        nm = MqttMsgType(ct).name.replace("ZZ_MQTT_CMD_", "").lower()
                    except Exception:
                        pass

                    if nm == "print_schedule":
                        ai_pause = obj.get("AIPausePrint", 0)
                        prog = obj.get("progress")
                        if ai_pause == 1:
                            now = time.time()
                            log(f"!! AI PAUSE flagged: {obj}")
                            capture_shot("aipause")
                            if AUTO_RESUME and now - last_ai > 90:
                                last_ai = now
                                ai_resume_count += 1
                                log(f"auto-resuming (BREAK_POINT 1039) attempt #{ai_resume_count}")
                                out = send(1039)
                                log(f"resume result: {out}")
                                time.sleep(2)
                                capture_shot("aipause-after")
                        if prog is not None:
                            if prog == last_progress:
                                if stall_start is None:
                                    stall_start = time.time()
                                elif time.time() - stall_start > 120:
                                    log(f"!! PROGRESS STALLED at {prog} for 2+ min — possible pause")
                                    stall_start = time.time()
                            else:
                                stall_start = None
                            last_progress = prog

                    elif nm == "event_notify":
                        st, val = obj.get("subType"), obj.get("value")
                        # value=1 subtype=1 is the normal ~3s heartbeat while printing
                        if val != 1 or st != 1:
                            log(f"EVENT non-heartbeat: subtype={st} value={val} {obj}")
                            if val == 8 and AUTO_RESUME and not last_event_was_pause:
                                last_event_was_pause = True
                                log("pause event (value=8) — BREAK_POINT resume")
                                out = send(1039)
                                log(f"resume result: {out}")
                            elif val == 1:
                                last_event_was_pause = False

                    elif nm in ("print_status", "system_check", "gcode_file_request"):
                        log(f"STATE {nm}: {obj}")

                if ONCE and time.time() > deadline:
                    break
        except Exception as e:
            log(f"stream error: {type(e).__name__} {e} — reconnect in 5s")
            time.sleep(5)
            try:
                client = open_client()
            except Exception:
                pass
    log(f"exiting (ai_resumes={ai_resume_count})")

if __name__ == "__main__":
    main()
