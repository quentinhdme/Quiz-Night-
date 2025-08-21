import streamlit as st
import pandas as pd
import json, os, hashlib, random, time
from datetime import datetime, timezone

st.set_page_config(page_title="Quiz Night Runden", page_icon="🕹️", layout="centered")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
QUESTIONS_CSV = os.path.join(DATA_DIR, "questions.csv")
ANSWERS_CSV = os.path.join(DATA_DIR, "answers.csv")
RATINGS_CSV = os.path.join(DATA_DIR, "ratings.csv")
PLAYERS_CSV = os.path.join(DATA_DIR, "players.csv")
STATE_JSON = os.path.join(DATA_DIR, "state.json")

# ---------- Helpers & State ----------
def ensure_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(QUESTIONS_CSV):
        pd.DataFrame(columns=["id","round_id","author","question","correct","wrong1","wrong2","wrong3","difficulty","created_at"]).to_csv(QUESTIONS_CSV, index=False)
    if not os.path.exists(ANSWERS_CSV):
        pd.DataFrame(columns=["timestamp","round_id","player","question_id","answer","is_correct"]).to_csv(ANSWERS_CSV, index=False)
    if not os.path.exists(RATINGS_CSV):
        pd.DataFrame(columns=["timestamp","round_id","player","question_id","stars"]).to_csv(RATINGS_CSV, index=False)
    if not os.path.exists(PLAYERS_CSV):
        pd.DataFrame(columns=["round_id","player","joined_at","last_seen"]).to_csv(PLAYERS_CSV, index=False)
    if not os.path.exists(STATE_JSON):
        init_state = {
            "round_id": 0,
            "phase": "lobby",  # lobby | write | answer | reveal | rate | results
            "phase_started_at": None,
            "paused": False,
            "pause_started_at": None,
            "accumulated_pause": 0.0,
            "question_order": [],
            "current_q_idx": 0,
            "host": {"name": None, "pin_hash": None},
            "durations": {"write": 60, "answer": 20, "reveal": 3, "rate": 10},
            "last_update": datetime.utcnow().isoformat() + "Z",
        }
        with open(STATE_JSON, "w", encoding="utf-8") as f:
            json.dump(init_state, f)

ensure_files()

@st.cache_data(ttl=1)
def load_df(path):
    return pd.read_csv(path)

def save_df(df, path):
    df.to_csv(path, index=False)

def load_state():
    with open(STATE_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    state["last_update"] = datetime.utcnow().isoformat() + "Z"
    with open(STATE_JSON, "w", encoding="utf-8") as f:
        json.dump(state, f)

def now_ts():
    return datetime.now(timezone.utc).timestamp()

def phase_elapsed(state):
    if state["phase_started_at"] is None:
        return 0.0
    elapsed = now_ts() - state["phase_started_at"] - state.get("accumulated_pause", 0.0)
    if state.get("paused") and state.get("pause_started_at"):
        elapsed -= (now_ts() - state["pause_started_at"])
    return max(0.0, elapsed)

def phase_remaining(state):
    dur = state["durations"].get(state["phase"], 0)
    rem = dur - phase_elapsed(state)
    return max(0, int(rem))

def phase_total(state):
    return state["durations"].get(state["phase"], 0)

# ---------- Session identity ----------
if "player_name" not in st.session_state:
    st.session_state["player_name"] = ""
if "is_host" not in st.session_state:
    st.session_state["is_host"] = False

# ---------- Styles ----------
PHASE_COLORS = {
    "lobby": "#f0f2f6",
    "write": "#cfe8ff",   # blau
    "answer": "#ffe1b3",  # orange
    "reveal": "#ffd6d6",  # rot/rosa
    "rate": "#d6f5d6",    # grün
    "results": "#ededed", # grau
}
state_for_style = load_state()
bg = PHASE_COLORS.get(state_for_style["phase"], "#f0f2f6")

st.markdown(f"""
<style>
footer {{visibility: hidden;}}
.block-container {{padding-top: 1.2rem; padding-bottom: 0.4rem;}}
html, body, [data-testid="stAppViewContainer"] > .main {{ background: {bg} !important; }}
.timer {{font-size: 2.2rem; font-weight: 800; margin: 0.5rem 0 0.4rem 0;}}
.phase {{font-size: 0.95rem; opacity: 0.85; margin-bottom: 0.2rem;}}
.progress-wrap {{margin: 0.2rem 0 1rem 0;}}
.footerq {{text-align:center; color:#888; margin-top:1rem;}}
.playerchip {{display:inline-block; padding:6px 10px; border-radius:16px; background:#fff; margin:4px; border:1px solid rgba(0,0,0,0.1);}}
.playerchip.me {{border:2px solid #2e7dff; font-weight:700;}}
</style>
""", unsafe_allow_html=True)

st.title("🕹️ Quiz Night — Rundenmodus (DE)")

# ---------- Sidebar: identity & host ----------
with st.sidebar:
    st.header("Spieler")
    name = st.text_input("Dein Name (einmal)", value=st.session_state["player_name"]).strip()
    if name and name != st.session_state["player_name"]:
        st.session_state["player_name"] = name
        state = load_state()
        pdf = load_df(PLAYERS_CSV)
        row = {"round_id": state["round_id"], "player": name, "joined_at": datetime.utcnow().isoformat(), "last_seen": datetime.utcnow().isoformat()}
        pdf = pd.concat([pdf, pd.DataFrame([row])], ignore_index=True)
        save_df(pdf, PLAYERS_CSV)
        st.rerun()

    # heartbeat
    if st.session_state["player_name"]:
        state = load_state()
        pdf = load_df(PLAYERS_CSV)
        if not pdf.empty:
            mask = (pdf["round_id"] == state["round_id"]) & (pdf["player"] == st.session_state["player_name"])
            if mask.any():
                pdf.loc[mask, "last_seen"] = datetime.utcnow().isoformat()
            else:
                row = {"round_id": state["round_id"], "player": st.session_state["player_name"], "joined_at": datetime.utcnow().isoformat(), "last_seen": datetime.utcnow().isoformat()}
                pdf = pd.concat([pdf, pd.DataFrame([row])], ignore_index=True)
            save_df(pdf, PLAYERS_CSV)

    st.divider()
    st.header("Host")
    state = load_state()
    if state["host"]["name"] and not st.session_state["is_host"]:
        pin_try = st.text_input("Host-PIN", type="password")
        if st.button("Als Host anmelden"):
            if state["host"]["pin_hash"] and hashlib.sha256(pin_try.encode()).hexdigest() == state["host"]["pin_hash"]:
                st.session_state["is_host"] = True
                st.success("Host-Modus aktiv.")
                st.rerun()
            else:
                st.error("Falsche PIN.")
    elif not state["host"]["name"]:
        new_host_name = st.text_input("Host-Name (erstellen)")
        new_pin = st.text_input("Neue Host-PIN", type="password")
        if st.button("Host erstellen"):
            if not new_host_name or not new_pin:
                st.error("Bitte Name und PIN eingeben.")
            else:
                state["host"]["name"] = new_host_name
                state["host"]["pin_hash"] = hashlib.sha256(new_pin.encode()).hexdigest()
                save_state(state)
                st.session_state["is_host"] = True
                st.success("Host erstellt und angemeldet.")
                st.rerun()
    else:
        st.success(f"Host: {state['host']['name']}")
        if st.button("Host abmelden"):
            st.session_state["is_host"] = False
            st.rerun()

# ---------- Host controls ----------
def start_phase(phase):
    state = load_state()
    state["phase"] = phase
    state["phase_started_at"] = now_ts()
    state["paused"] = False
    state["pause_started_at"] = None
    state["accumulated_pause"] = 0.0
    save_state(state)

def reset_round(new_round=True):
    state = load_state()
    if new_round:
        state["round_id"] += 1
    state["phase"] = "lobby"
    state["phase_started_at"] = None
    state["paused"] = False
    state["pause_started_at"] = None
    state["accumulated_pause"] = 0.0
    state["question_order"] = []
    state["current_q_idx"] = 0
    save_state(state)

def advance(force=False):
    state = load_state()
    if state["paused"] and not force:
        return
    rem = phase_remaining(state)
    if rem > 0 and not force:
        return
    if state["phase"] == "write":
        qdf = load_df(QUESTIONS_CSV)
        qids = qdf[qdf["round_id"] == state["round_id"]]["id"].tolist()
        random.shuffle(qids)
        state["question_order"] = qids
        state["current_q_idx"] = 0
        save_state(state)
        if len(qids) == 0:
            # keine Fragen -> zurück zur Lobby
            reset_round(new_round=False)
        else:
            start_phase("answer")
    elif state["phase"] == "answer":
        start_phase("reveal")
    elif state["phase"] == "reveal":
        start_phase("rate")
    elif state["phase"] == "rate":
        if state["current_q_idx"] + 1 < len(state["question_order"]):
            state["current_q_idx"] += 1
            save_state(state)
            start_phase("answer")
        else:
            start_phase("results")

def host_controls():
    state = load_state()
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("⏯️ Pause/Weiter"):
            state = load_state()
            if state["paused"]:
                if state["pause_started_at"]:
                    state["accumulated_pause"] += (now_ts() - state["pause_started_at"])
                state["paused"] = False
                state["pause_started_at"] = None
            else:
                state["paused"] = True
                state["pause_started_at"] = now_ts()
            save_state(state)
            st.rerun()
    with c2:
        if st.button("⏭️ Nächste Phase/Frage"):
            advance(force=True)
            st.rerun()
    with c3:
        if st.button("🔁 Runde neu starten"):
            reset_round(new_round=False)
            st.rerun()
    with c4:
        if st.button("🆕 Neue Runde"):
            reset_round(new_round=True)
            st.rerun()

# ---------- Shared UI Bits ----------
def phase_header(title, show_progress=True):
    state = load_state()
    rem = phase_remaining(state)
    total = phase_total(state)
    st.subheader(title)
    st.markdown(f"<div class='phase'>Runden-ID: {state['round_id']}</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='timer'>⏳ {rem} s</div>", unsafe_allow_html=True)
    if show_progress and total > 0:
        progress = (total - rem) / total
        st.progress(progress, text=f"{total - rem}/{total} s")

    # Auto-advance if time is up
    if rem == 0:
        advance()

    if st.session_state["is_host"]:
        host_controls()

# ---------- Lobby list ----------
def lobby_list():
    state = load_state()
    pdf = load_df(PLAYERS_CSV)
    names = []
    if not pdf.empty:
        names = pdf[pdf["round_id"] == state["round_id"]]["player"].dropna().unique().tolist()
    if names:
        st.caption("Spieler in der Lobby:")
        chips = []
        me = st.session_state["player_name"]
        for n in sorted(names, key=lambda x: x.lower()):
            cls = "playerchip me" if me and n == me else "playerchip"
            chips.append(f"<span class='{cls}'>{n}</span>")
        st.markdown(" ".join(chips), unsafe_allow_html=True)
    else:
        st.caption("Noch keine Spieler in der Lobby.")

# ---------- Views ----------
def view_lobby():
    state = load_state()
    st.subheader("👥 Lobby")
    st.caption(f"Aktuelle Runden-ID: {state['round_id']}")
    lobby_list()
    if st.session_state["is_host"]:
        st.info("Phase: Lobby — bereit zum Starten.")
        if st.button("▶️ Runde starten (Phase: Frage schreiben, 60s)"):
            if state["round_id"] == 0:
                state["round_id"] = 1
            save_state(state)
            start_phase("write")
            st.rerun()
    else:
        st.write("Warte auf Start der Runde durch den Host.")

def view_write():
    phase_header("📝 Phase 1: Frage schreiben (60s)")
    state = load_state()
    name = st.session_state["player_name"]
    if not name:
        st.warning("Bitte gib links deinen Namen ein.")
        return

    # ensure presence
    pdf = load_df(PLAYERS_CSV)
    if not pdf.empty:
        mask = (pdf["round_id"] == state["round_id"]) & (pdf["player"] == name)
        if not mask.any():
            row = {"round_id": state["round_id"], "player": name, "joined_at": datetime.utcnow().isoformat(), "last_seen": datetime.utcnow().isoformat()}
            pdf = pd.concat([pdf, pd.DataFrame([row])], ignore_index=True)
            save_df(pdf, PLAYERS_CSV)

    qdf = load_df(QUESTIONS_CSV)
    already = False
    if not qdf.empty:
        already = any((qdf['round_id']==state['round_id']) & (qdf['author']==name))
    if already:
        st.success("✅ Deine Frage für diese Runde ist eingereicht.")
    else:
        with st.form("write"):
            q = st.text_area("Deine Frage")
            c = st.text_input("Richtige Antwort")
            w1 = st.text_input("Falsche Antwort 1")
            w2 = st.text_input("Falsche Antwort 2 (optional)")
            w3 = st.text_input("Falsche Antwort 3 (optional)")
            ok = st.form_submit_button("Einreichen")
        if ok:
            if not q or not c or not w1:
                st.error("Bitte Frage, richtige Antwort und mindestens eine falsche Antwort ausfüllen.")
            else:
                qdf = load_df(QUESTIONS_CSV)
                new_id = int((qdf["id"].max()+1) if not qdf.empty else 1)
                new_row = {
                    "id": new_id, "round_id": state["round_id"], "author": name,
                    "question": q, "correct": c, "wrong1": w1, "wrong2": w2, "wrong3": w3,
                    "difficulty": "n/a", "created_at": datetime.utcnow().isoformat()
                }
                qdf = pd.concat([qdf, pd.DataFrame([new_row])], ignore_index=True)
                save_df(qdf, QUESTIONS_CSV)
                st.success("Gespeichert.")
                st.rerun()

def view_answer():
    phase_header("🎮 Phase 2: Beantworten (20s)")
    state = load_state()
    qdf = load_df(QUESTIONS_CSV)
    if len(state["question_order"]) == 0:
        st.warning("Keine Fragen vorhanden. Zurück zur Lobby.")
        return
    qid = state["question_order"][state["current_q_idx"]]
    q = qdf[qdf["id"] == qid].iloc[0]

    st.markdown(f"**Frage {state['current_q_idx']+1}/{len(state['question_order'])}:** {q['question']}")

    opts = [q["correct"], q["wrong1"]]
    if isinstance(q["wrong2"], str) and q["wrong2"].strip(): opts.append(q["wrong2"])
    if isinstance(q["wrong3"], str) and q["wrong3"].strip(): opts.append(q["wrong3"])
    random.seed(qid)
    random.shuffle(opts)

    name = st.session_state["player_name"]
    if not name:
        st.warning("Bitte gib links deinen Namen ein.")
        return

    if name == q["author"]:
        st.info("🙅‍♂️ Das ist deine eigene Frage — du darfst sie nicht beantworten.")
        st.radio("Antwortoptionen (deaktiviert):", opts, index=None, disabled=True)
        return

    adf = load_df(ANSWERS_CSV)
    answered = False
    if not adf.empty:
        answered = any((adf['round_id']==state['round_id']) & (adf['player']==name) & (adf['question_id']==qid))
    if answered:
        st.info("✅ Antwort gespeichert.")
    else:
        with st.form(f"a{qid}"):
            choice = st.radio("Deine Antwort:", opts, index=None)
            ok = st.form_submit_button("Senden")
        if ok:
            if choice is None:
                st.error("Bitte eine Antwort wählen.")
            else:
                is_correct = (choice == q["correct"])
                adf = load_df(ANSWERS_CSV)
                new_row = {"timestamp": datetime.utcnow().isoformat(), "round_id": state["round_id"],
                           "player": name, "question_id": int(qid), "answer": choice, "is_correct": bool(is_correct)}
                adf = pd.concat([adf, pd.DataFrame([new_row])], ignore_index=True)
                save_df(adf, ANSWERS_CSV)
                st.success("Antwort gespeichert.")
                st.rerun()

def view_reveal():
    phase_header("🔔 Reveal: Richtige Antwort (3s)")
    state = load_state()
    qdf = load_df(QUESTIONS_CSV)
    if len(state["question_order"]) == 0:
        st.warning("Keine Fragen vorhanden.")
        return
    qid = state["question_order"][state["current_q_idx"]]
    q = qdf[qdf["id"] == qid].iloc[0]
    st.markdown(f"**Richtige Antwort:** ✅ **{q['correct']}**")
    st.caption(f"Autor: {q['author']}")

def view_rate():
    phase_header("⭐ Phase 3: Bewerten (10s)")
    state = load_state()
    qdf = load_df(QUESTIONS_CSV)
    if len(state["question_order"]) == 0:
        st.warning("Keine Fragen.")
        return
    qid = state["question_order"][state["current_q_idx"]]
    q = qdf[qdf["id"] == qid].iloc[0]
    st.markdown(f"**Frage {state['current_q_idx']+1}/{len(state['question_order'])}:** {q['question']}")

    name = st.session_state["player_name"]
    if not name:
        st.warning("Bitte gib links deinen Namen ein.")
        return

    if name == q["author"]:
        st.info("🙅‍♂️ Eigene Frage — Bewertung deaktiviert.")
        st.slider("Sterne (deaktiviert)", 1, 5, 4, disabled=True)
        return

    rdf = load_df(RATINGS_CSV)
    rated = False
    if not rdf.empty:
        rated = any((rdf['round_id']==state['round_id']) & (rdf['player']==name) & (rdf['question_id']==qid))
    if rated:
        st.info("✅ Bewertung gespeichert.")
    else:
        with st.form(f"r{qid}"):
            stars = st.slider("Sterne", 1, 5, 4)
            ok = st.form_submit_button("Bewerten")
        if ok:
            rdf = load_df(RATINGS_CSV)
            new_row = {"timestamp": datetime.utcnow().isoformat(), "round_id": state["round_id"],
                       "player": name, "question_id": int(qid), "stars": int(stars)}
            rdf = pd.concat([rdf, pd.DataFrame([new_row])], ignore_index=True)
            save_df(rdf, RATINGS_CSV)
            st.success("Bewertung gespeichert.")
            st.rerun()

def compute_scores(round_id):
    qdf = load_df(QUESTIONS_CSV); adf = load_df(ANSWERS_CSV); rdf = load_df(RATINGS_CSV)
    qdf = qdf[qdf["round_id"]==round_id].copy()
    adf = adf[adf["round_id"]==round_id].copy()
    rdf = rdf[rdf["round_id"]==round_id].copy()

    # Spielerpunkte
    if not adf.empty:
        ppts = adf.groupby("player")["is_correct"].sum().mul(10).rename("Spielerpunkte")
    else:
        ppts = pd.Series(dtype=float, name="Spielerpunkte")

    # Autorenpunkte
    rows = []
    for _, q in qdf.iterrows():
        qid = int(q["id"])
        sub = adf[adf["question_id"] == qid]
        N = len(sub); C = int(sub["is_correct"].sum()) if N>0 else 0
        base = 0
        if N>0:
            base = 5 * max(N - C, 0)
            if C==0: base = 2
            elif C==N: base = 0
        rsub = rdf[rdf["question_id"]==qid]
        stars_avg = rsub["stars"].mean() if len(rsub)>0 else 3.0
        mult = 0.2*stars_avg + 0.4
        rows.append({"author": q["author"], "author_points": base*mult})
    ap = pd.DataFrame(rows)
    if not ap.empty:
        apts = ap.groupby("author")["author_points"].sum().rename("Autorenpunkte")
    else:
        apts = pd.Series(dtype=float, name="Autorenpunkte")

    total = pd.concat([ppts, apts], axis=1)

    if "Spielerpunkte" not in total.columns:
        total["Spielerpunkte"] = 0.0
    if "Autorenpunkte" not in total.columns:
        total["Autorenpunkte"] = 0.0

    total = total.fillna(0.0)
    total["Gesamt"] = total["Spielerpunkte"].astype(float) + total["Autorenpunkte"].astype(float)

    if total.empty:
        return pd.DataFrame(columns=["Name", "Spielerpunkte", "Autorenpunkte", "Gesamt"])

    total = total.sort_values("Gesamt", ascending=False).reset_index().rename(columns={"index":"Name"})
    return total

def view_results():
    # Kein Progress/Takt mehr nötig
    st.subheader("🏆 Ergebnisse dieser Runde")
    state = load_state()
    df = compute_scores(state["round_id"])
    if df is None or df.empty:
        st.info("Noch keine Daten in dieser Runde.")
    else:
        st.dataframe(df.style.format({"Spielerpunkte":"{:.0f}","Autorenpunkte":"{:.1f}","Gesamt":"{:.1f}"}), use_container_width=True)
    if st.session_state["is_host"]:
        host_controls()
        if st.button("▶️ Neue Schreib-Phase (60s)"):
            start_phase("write")
            st.rerun()

# ---------- Router ----------
state = load_state()
phase = state["phase"]
phase_names = {
    "lobby": "Lobby",
    "write": "Frage schreiben",
    "answer": "Beantworten",
    "reveal": "Reveal",
    "rate": "Bewerten",
    "results": "Ergebnisse"
}
st.markdown(f"**Aktuelle Phase:** {phase_names.get(phase, phase)}")

if phase == "lobby":
    view_lobby()
elif phase == "write":
    view_write()
elif phase == "answer":
    view_answer()
elif phase == "reveal":
    view_reveal()
elif phase == "rate":
    view_rate()
elif phase == "results":
    view_results()
else:
    st.error("Unbekannte Phase. Zurück zur Lobby.")
    reset_round(new_round=False)

# ---------- Auto-refresh once per second across ALL phases (for tight sync) ----------
# We keep it gentle: 1s tick; when paused, stop ticking.
state = load_state()
phase = state["phase"]
st.caption("⏱️ Live-Update aktiv")
if phase in ("write", "answer", "reveal", "rate", "lobby"):
    if not state.get("paused"):
        time.sleep(1)
        st.rerun()

st.markdown("<div class='footerq'>Made by Quirlin</div>", unsafe_allow_html=True)
