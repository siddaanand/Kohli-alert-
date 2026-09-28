"""
Player run alarm - scheduled GitHub Actions version (free).

Settings come from GitHub repository variables (no code edits needed):
  PLAYERS   = comma-separated player names, e.g.  virat kohli, rohit sharma
  TEAMS     = comma-separated teams to watch, e.g. india, royal challengers
  THRESHOLD = alert when a player reaches this many runs, e.g. 40 (or 1, or 100)

Each run (every 10 min, see kohli.yml):
  1. Finds live matches involving your TEAMS (1 API call).
  2. After the toss, sends ONE quiet info notification per match.
  3. Only when one of your teams is batting, fetches the scorecard (1 API call per match).
  4. Sends ONE loud alert per player per match when they reach THRESHOLD.
Duplicates are avoided by checking the last 12h of your ntfy topic.
"""

import os
import json
import requests


def as_list(value, default):
    items = [x.strip().lower() for x in (value or default).split(",")]
    return [x for x in items if x]


API_KEY = os.environ["CRICAPI_KEY"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
PLAYERS = as_list(os.environ.get("PLAYERS"), "virat kohli")
TEAMS = as_list(os.environ.get("TEAMS"), "india, royal challengers")
THRESHOLD = int((os.environ.get("THRESHOLD") or "40").strip())
BASE = "https://api.cricapi.com/v1"
NTFY = f"https://ntfy.sh/{NTFY_TOPIC}"


def get(endpoint, **params):
    params["apikey"] = API_KEY
    r = requests.get(f"{BASE}/{endpoint}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def live_matches():
    """Return [(match, [your teams in this match])] for live matches."""
    found = []
    for m in get("currentMatches", offset=0).get("data", []) or []:
        if not m.get("matchStarted") or m.get("matchEnded"):
            continue
        mine = [t for t in m.get("teams", []) if any(k in t.lower() for k in TEAMS)]
        if mine:
            found.append((m, mine))
    return found


def team_batting(match, teams):
    """True once any of your teams has an innings in the score list."""
    for s in match.get("score", []) or []:
        inning = (s.get("inning", "") or "").lower()
        if any(t.lower() in inning for t in teams):
            return True
    return False


def player_runs(match_id):
    """Return {player keyword: runs} for your PLAYERS found in the scorecard."""
    data = get("match_scorecard", id=match_id).get("data", {}) or {}
    runs = {}
    for innings in data.get("scorecard", []) or []:
        for b in innings.get("batting", []) or []:
            name = (b.get("batsman", {}) or {}).get("name", "").lower()
            for p in PLAYERS:
                if p in name:
                    runs[p] = int(b.get("r", 0))
    return runs


def recent_messages():
    """All ntfy messages from the last 12h, joined into one string."""
    try:
        r = requests.get(f"{NTFY}/json", params={"poll": "1", "since": "12h"}, timeout=15)
        msgs = []
        for line in r.text.splitlines():
            try:
                msgs.append(json.loads(line).get("message", ""))
            except ValueError:
                pass
        return "\n".join(msgs)
    except requests.RequestException:
        return ""


def notify(title, message, priority):
    requests.post(
        NTFY,
        data=message.encode(),
        headers={"Title": title, "Priority": str(priority), "Tags": "cricket_game"},
        timeout=15,
    )


def toss_update(match, teams, history):
    winner = match.get("tossWinner")
    choice = (match.get("tossChoice") or "").lower()
    marker = f"[toss {match['id']}]"
    if not winner or not choice or marker in history:
        return
    parts = []
    for t in teams:
        won = t.lower() in winner.lower()
        first = (won and "bat" in choice) or (not won and "bowl" in choice)
        parts.append(f"{t} bat {'1st' if first else '2nd'}.")
    who = ", ".join(p.title() for p in PLAYERS)
    notify("Toss update", f"{winner} chose to {choice}. {' '.join(parts)} Watching: {who}. {marker}", priority=2)


def main():
    print(f"Players: {PLAYERS} | Teams: {TEAMS} | Threshold: {THRESHOLD}")
    matches = live_matches()
    if not matches:
        print("No live match for your teams right now.")
        return

    history = recent_messages()
    for match, teams in matches:
        mid = match["id"]
        print(f"Live match: {match.get('name')} | toss: {match.get('tossWinner')} {match.get('tossChoice')}")
        toss_update(match, teams, history)

        if not team_batting(match, teams):
            print(f"{', '.join(teams)} not batting yet - skipping scorecard call.")
            continue

        pending = [p for p in PLAYERS if f"[alert {mid} {p}]" not in history]
        if not pending:
            print("All players already alerted for this match.")
            continue

        runs = player_runs(mid)
        for p in pending:
            r = runs.get(p)
            print(f"{p}: {r}")
            if r is not None and r >= THRESHOLD:
                notify(
                    f"{p.title()} {THRESHOLD}+ ALERT",
                    f"{p.title()} is on {r}* - switch on the match! [alert {mid} {p}]",
                    priority=5,
                )
                print(f"Alert sent for {p}.")


if __name__ == "__main__":
    main()
