import streamlit as st
import pandas as pd
import json, os, hashlib, random, time
from datetime import datetime, timezone

st.set_page_config(page_title="Quiz Night – Sync Pro", page_icon="🕹️", layout="centered")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
QUESTIONS_CSV = os.path.join(DATA_DIR, "questions.csv")
ANSWERS_CSV = os.path.join(DATA_DIR, "answers.csv")
RATINGS_CSV = os.path.join(DATA_DIR, "ratings.csv")
PLAYERS_CSV = os.path.join(DATA_DIR, "players.csv")
STATE_JSON = os.path.join(DATA_DIR, "state.json")

HEARTBEAT_SEC = 2       # Präsenz-Schreibintervall
SYNC_TICK_SEC = 0.25    # UI-Refresh-Intervall

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
        pd.DataFrame(columns=["round_id","player","joined_at","last_seen","phase"]).to_csv(PLAYERS_CSV, index=False)
    else:
        # Falls aus älteren Versionen ohne "phase"
        df = pd.read_csv(PLAYERS_CSV)
        if "phase" not in df.columns:
            df["phase"] = ""
            df.to_csv(PLAYERS_CSV, index=False)
    if not os.path.exists(STATE_JSON):
        init_state = {
            "round_id": 0,
            "phase": "lobby",  # lobby | write | answer | reveal | rate | results
            "phase_started_at": None,
            "question_order": [],
            "current_q_idx": 0,
            "host": {"name": None, "pin_hash": None},
            "last_update": datetime.utcnow().isoformat() + "Z",  # jede Änderung erhöht das
            "sync_nonce": 0,   # Host kann erhöhen ⇒ Clients erzwingen 1x neu ziehen
        }
        with open(STATE_JSON, "w", encoding="utf-8") as f:
            json.dump(init_state, f)

ensure_files()

def load_df(path):
    # bewusst ohne Cache für harte Synchronisation
    return pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()

def save_df(df, path):
    df.to_csv(path, index=False)

def load_state():
    with open(STATE_JSON, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state):
    state["last_update"] = datetime.utcnow().isoformat() + "Z"
    with open(STATE_JSON, "w", encoding="utf-8") as f:
        json.dump(state, f)

def utc_now_iso():
    return datetime.utcnow().isoformat()

def iso_to_ts(iso):
    try:
        # strip Z if present
        s = iso.replace("Z","")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0

# ---------- Session identity ----------
if "player_name" not in st.session_state:
    st.session_state["player_name"] = ""
if "is_host" not in st.session_state:
    st.session_state["is_host"] = False
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "last_presence_write" not in st.session_state:
    st.session_state["last_presence_write"] = 0.0
if "last_update_seen" not in st.session_state:
    st.session_state["last_update_seen"] = ""
if "sync_nonce_seen" not in st.session_state:
    st.session_state["sync_nonce_seen"] = 0

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
.block-container {{padding-top: 1.0rem; padding-bottom: 0.4rem;}}
html, body, [data-testid="stAppViewContainer"] > .main {{ background: {bg} !important; }}
.phase {{font-size: 0.95rem; opacity: 0.85; margin-bottom: 0.2rem;}}
.footerq {{text-align:center; color:#888; margin-top:1rem;}}
.playerchip {{display:inline-flex; align-items:center; gap:6px; padding:6px 10px; border-radius:16px; background:#fff; margin:4px; border:1px solid rgba(0,0,0,0.1);}}
.playerchip.me {{border:2px solid #2e7dff; font-weight:700;}}
.dot {{width:8px; height:8px; border-radius:50%; display:inline-block;}}
.dot.green {{background:#33c24d;}}
.dot.gray {{background:#bbb;}}
</style>
""", unsafe_allow_html=True)

st.title("🕹️ Quiz Night — Sync Pro (DE)")

# ---------- Presence ----------
def update_presence(phase_for_player):
    if not st.session_state.get("logged_in"): 
        return
    now_ts = time.time()
    if now_ts - st.session_state["last_presence_write"] < HEARTBEAT_SEC:
        return
    st.session_state["last_presence_write"] = now_ts
    state = load_state()
    pdf = load_df(PLAYERS_CSV)
    if pdf.empty:
        row = {"round_id": state["round_id"], "player": st.session_state["player_name"], "joined_at": utc_now_iso(), "last_seen": utc_now_iso(), "phase": phase_for_player}
        save_df(pd.DataFrame([row]), PLAYERS_CSV)
        return
    # ensure columns
    if "phase" not in pdf.columns:
        pdf["phase"] = ""
    mask = (pdf["round_id"] == state["round_id"]) & (pdf["player"] == st.session_state["player_name"])
    if mask.any():
        pdf.loc[mask, "last_seen"] = utc_now_iso()
        pdf.loc[mask, "phase"] = phase_for_player
    else:
        row = {"round_id": state["round_id"], "player": st.session_state["player_name"], "joined_at": utc_now_iso(), "last_seen": utc_now_iso(), "phase": phase_for_player}
        pdf = pd.concat([pdf, pd.DataFrame([row])], ignore_index=True)
    save_df(pdf, PLAYERS_CSV)

# ---------- Sidebar: identity & host ----------
with st.sidebar:
    st.header("Spieler")
    name_input = st.text_input("Dein Name", value=st.session_state["player_name"]).strip()
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Beitreten", use_container_width=True):
            if not name_input:
                st.error("Bitte gib zuerst einen Namen ein.")
            else:
                st.session_state["player_name"] = name_input
                st.session_state["logged_in"] = True
                # sofort Präsenz schreiben
                update_presence("lobby")
                st.success("✅ Eingeloggt")
                st.rerun()
    with c2:
        if st.session_state["logged_in"]:
            if st.button("Logout", use_container_width=True):
                st.session_state["logged_in"] = False
                st.session_state["player_name"] = ""
                st.rerun()

    # Host
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

# ---------- Host controls (manual; no timers) ----------
def start_phase(phase):
    state = load_state()
    state["phase"] = phase
    state["phase_started_at"] = utc_now_iso() + "Z"
    save_state(state)

def reset_round(new_round=True):
    state = load_state()
    if new_round:
        state["round_id"] += 1
    state["phase"] = "lobby"
    state["phase_started_at"] = None
    state["question_order"] = []
    state["current_q_idx"] = 0
    save_state(state)

def prepare_questions_for_round():
    state = load_state()
    qdf = load_df(QUESTIONS_CSV)
    qids = qdf[qdf["round_id"] == state["round_id"]]["id"].tolist()
    random.shuffle(qids)
    state["question_order"] = qids
    state["current_q_idx"] = 0
    save_state(state)
    return qids

def advance():
    state = load_state()
    if state["phase"] == "write":
        qids = prepare_questions_for_round()
        if len(qids) == 0:
            st.warning("Es gibt noch keine Fragen in dieser Runde.")
            return
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

def force_sync():
    state = load_state()
    state["sync_nonce"] = int(state.get("sync_nonce", 0)) + 1
    save_state(state)

def host_controls():
    st.write("")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("▶️ Weiter", use_container_width=True):
            advance(); st.rerun()
    with c2:
        if st.button("🔁 Runde neu", use_container_width=True):
            reset_round(new_round=False); st.rerun()
    with c3:
        if st.button("🆕 Neue Runde", use_container_width=True):
            reset_round(new_round=True); st.rerun()
    with c4:
        if st.button("🛰 Force Sync", use_container_width=True):
            force_sync(); st.rerun()

# ---------- Lobby list with active dots ----------
def lobby_list():
    state = load_state()
    pdf = load_df(PLAYERS_CSV)
    names = []
    if not pdf.empty:
        # ensure columns
        for col in ["phase","last_seen"]:
            if col not in pdf.columns:
                pdf[col] = ""
        # active if last_seen <= 10s ago
        now_ts = time.time()
        rows = pdf[pdf["round_id"] == state["round_id"]][["player","last_seen","phase"]].dropna().drop_duplicates()
        chips = []
        me = st.session_state["player_name"] if st.session_state.get("logged_in") else ""
        for _, r in rows.iterrows():
            p = str(r["player"])
            ls = str(r["last_seen"])
            ph = str(r["phase"])
            active = (now_ts - iso_to_ts(ls)) <= 10.0
            dot_cls = "green" if active else "gray"
            cls = "playerchip me" if me and p == me else "playerchip"
            chips.append(f"<span class='{cls}'><span class='dot {dot_cls}'></span>{p}</span>")
        if chips:
            st.markdown(" ".join(chips), unsafe_allow_html=True)
        else:
            st.caption("Noch keine Spieler in der Lobby.")
    else:
        st.caption("Noch keine Spieler in der Lobby.")

# ---------- Sync dashboard (Host only) ----------
def host_sync_dashboard():
    state = load_state()
    pdf = load_df(PLAYERS_CSV)
    if pdf.empty:
        st.info("Keine Spieler erfasst.")
        return
    now_ts = time.time()
    cur_phase = state["phase"]
    # ensure columns
    if "phase" not in pdf.columns: pdf["phase"] = ""
    if "last_seen" not in pdf.columns: pdf["last_seen"] = ""
    sub = pdf[pdf["round_id"] == state["round_id"]].copy()
    sub["active"] = sub["last_seen"].apply(lambda s: (now_ts - iso_to_ts(str(s))) <= 10.0)
    total = len(sub["player"].unique())
    sync_ok = len(sub[(sub["phase"] == cur_phase) & (sub["active"])]["player"].unique())
    st.caption(f"🔎 Sync: {sync_ok}/{total} Spieler in Phase „{cur_phase}“ (aktiv)")

# ---------- Views ----------
def view_lobby():
    state = load_state()
    st.subheader("👥 Lobby")
    st.caption(f"Runde: {state['round_id']}")
    lobby_list()
    if st.session_state["is_host"]:
        host_sync_dashboard()
        st.info("Host steuert den Ablauf. Erst Runde starten, dann mit ▶️ weiter.")
        if st.button("🚀 Runde starten (Schreib-Phase)"):
            if state["round_id"] == 0:
                state["round_id"] = 1
                save_state(state)
            start_phase("write"); st.rerun()
    else:
        st.write("Warte auf Start der Runde durch den Host.")

def view_write():
    update_presence("write")
    state = load_state()
    st.subheader("📝 Phase 1: Frage schreiben")
    if st.session_state.get("logged_in") is not True:
        st.warning("Bitte links Namen eingeben und **Beitreten** drücken."); return
    name = st.session_state["player_name"]

    qdf = load_df(QUESTIONS_CSV)
    already = False
    if not qdf.empty:
        already = any((qdf['round_id']==state['round_id']) & (qdf['author']==name))
    if already:
        st.success("✅ Deine Frage ist eingereicht. Warte auf ▶️ vom Host.")
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
                qdf2 = load_df(QUESTIONS_CSV)
                new_id = int((qdf2["id"].max()+1) if not qdf2.empty else 1)
                new_row = {"id": new_id, "round_id": state["round_id"], "author": name,
                           "question": q, "correct": c, "wrong1": w1, "wrong2": w2, "wrong3": w3,
                           "difficulty": "n/a", "created_at": utc_now_iso()}
                qdf2 = pd.concat([qdf2, pd.DataFrame([new_row])], ignore_index=True)
                save_df(qdf2, QUESTIONS_CSV)
                st.success("Gespeichert."); st.rerun()
    if st.session_state["is_host"]:
        host_controls(); host_sync_dashboard()

def view_answer():
    update_presence("answer")
    state = load_state()
    st.subheader("🎮 Phase 2: Beantworten")
    qdf = load_df(QUESTIONS_CSV)
    if len(state["question_order"]) == 0:
        st.warning("Keine Fragen vorhanden. Host: drücke 🔁 oder starte neu.")
        if st.session_state["is_host"]: host_controls(); host_sync_dashboard()
        return
    qid = state["question_order"][state["current_q_idx"]]
    q = qdf[qdf["id"] == qid].iloc[0]
    st.markdown(f"**Frage {state['current_q_idx']+1}/{len(state['question_order'])}:** {q['question']}")

    opts = [q["correct"], q["wrong1"]]
    if isinstance(q["wrong2"], str) and str(q["wrong2"]).strip(): opts.append(q["wrong2"])
    if isinstance(q["wrong3"], str) and str(q["wrong3"]).strip(): opts.append(q["wrong3"])
    random.seed(qid); random.shuffle(opts)

    if st.session_state.get("logged_in") is not True:
        st.warning("Bitte links Namen eingeben und **Beitreten** drücken."); return
    name = st.session_state["player_name"]
    if name == q["author"]:
        st.info("🙅‍♂️ Eigene Frage — nicht beantwortbar.")
        st.radio("Antwortoptionen (deaktiviert):", opts, index=None, disabled=True)
    else:
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
                    new_row = {"timestamp": utc_now_iso(), "round_id": state["round_id"], "player": name,
                               "question_id": int(qid), "answer": choice, "is_correct": bool(is_correct)}
                    adf = pd.concat([adf, pd.DataFrame([new_row])], ignore_index=True)
                    save_df(adf, ANSWERS_CSV)
                    st.success("Antwort gespeichert."); st.rerun()
    if st.session_state["is_host"]:
        host_controls(); host_sync_dashboard()

def view_reveal():
    update_presence("reveal")
    state = load_state()
    st.subheader("🔔 Reveal: Richtige Antwort")
    if len(state["question_order"]) == 0:
        st.warning("Keine Fragen vorhanden."); 
        if st.session_state["is_host"]: host_controls(); host_sync_dashboard()
        return
    qdf = load_df(QUESTIONS_CSV)
    qid = state["question_order"][state["current_q_idx"]]
    q = qdf[qdf["id"] == qid].iloc[0]
    st.markdown(f"**Richtige Antwort:** ✅ **{q['correct']}**")
    st.caption(f"Autor: {q['author']}")
    if st.session_state["is_host"]:
        host_controls(); host_sync_dashboard()

def view_rate():
    update_presence("rate")
    state = load_state()
    st.subheader("⭐ Phase 3: Bewerten")
    if len(state["question_order"]) == 0:
        st.warning("Keine Fragen."); 
        if st.session_state["is_host"]: host_controls(); host_sync_dashboard()
        return
    qdf = load_df(QUESTIONS_CSV)
    qid = state["question_order"][state["current_q_idx"]]
    q = qdf[qdf["id"] == qid].iloc[0]
    st.markdown(f"**Frage {state['current_q_idx']+1}/{len(state['question_order'])}:** {q['question']}")

    if st.session_state.get("logged_in") is not True:
        st.warning("Bitte links Namen eingeben und **Beitreten** drücken."); 
        if st.session_state["is_host"]: host_controls(); host_sync_dashboard()
        return
    name = st.session_state["player_name"]
    if name == q["author"]:
        st.info("🙅‍♂️ Eigene Frage — Bewertung deaktiviert.")
        st.slider("Sterne (deaktiviert)", 1, 5, 4, disabled=True)
    else:
        rdf = load_df(RATINGS_CSV); rated = False
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
                new_row = {"timestamp": utc_now_iso(), "round_id": state["round_id"], "player": name, "question_id": int(qid), "stars": int(stars)}
                rdf = pd.concat([rdf, pd.DataFrame([new_row])], ignore_index=True)
                save_df(rdf, RATINGS_CSV)
                st.success("Bewertung gespeichert."); st.rerun()
    if st.session_state["is_host"]:
        host_controls(); host_sync_dashboard()

def compute_scores(round_id):
    qdf = load_df(QUESTIONS_CSV); adf = load_df(ANSWERS_CSV); rdf = load_df(RATINGS_CSV)
    qdf = qdf[qdf["round_id"]==round_id].copy()
    adf = adf[adf["round_id"]==round_id].copy()
    rdf = rdf[rdf["round_id"]==round_id].copy()

    if not adf.empty:
        ppts = adf.groupby("player")["is_correct"].sum().mul(10).rename("Spielerpunkte")
    else:
        ppts = pd.Series(dtype=float, name="Spielerpunkte")

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
    if "Spielerpunkte" not in total.columns: total["Spielerpunkte"] = 0.0
    if "Autorenpunkte" not in total.columns: total["Autorenpunkte"] = 0.0
    total = total.fillna(0.0)
    total["Gesamt"] = total["Spielerpunkte"].astype(float) + total["Autorenpunkte"].astype(float)

    if total.empty:
        return pd.DataFrame(columns=["Name", "Spielerpunkte", "Autorenpunkte", "Gesamt"])
    total = total.sort_values("Gesamt", ascending=False).reset_index().rename(columns={"index":"Name"})
    return total

def view_results():
    update_presence("results")
    state = load_state()
    st.subheader("🏆 Ergebnisse")
    df = compute_scores(state["round_id"])
    if df is None or df.empty:
        st.info("Noch keine Daten in dieser Runde.")
    else:
        st.dataframe(df.style.format({"Spielerpunkte":"{:.0f}","Autorenpunkte":"{:.1f}","Gesamt":"{:.1f}"}), use_container_width=True)
    if st.session_state["is_host"]:
        host_controls(); host_sync_dashboard()

# ---------- Router & instant sync trigger ----------
state = load_state()

# Instant phase-change detection via last_update / sync_nonce
if st.session_state["last_update_seen"] and st.session_state["last_update_seen"] != state.get("last_update"):
    st.session_state["last_update_seen"] = state.get("last_update")
    st.rerun()
else:
    st.session_state["last_update_seen"] = state.get("last_update")

if st.session_state["sync_nonce_seen"] != int(state.get("sync_nonce", 0)):
    st.session_state["sync_nonce_seen"] = int(state.get("sync_nonce", 0))
    st.rerun()

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
    update_presence("lobby")
    st.subheader("👥 Lobby")
    st.caption(f"Runde: {state['round_id']}")
    lobby_list()
    if st.session_state["is_host"]:
        host_sync_dashboard()
        st.info("Host steuert den Ablauf. Erst Runde starten, dann mit ▶️ weiter.")
        if st.button("🚀 Runde starten (Schreib-Phase)"):
            if state["round_id"] == 0:
                state["round_id"] = 1; save_state(state)
            start_phase("write"); st.rerun()
    else:
        st.write("Warte auf Start der Runde durch den Host.")
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

# ---------- Stronger sync: global 0.25s tick ----------
st.caption("🔄 Sync aktiv (0.25s) — Host steuert den Ablauf")
time.sleep(SYNC_TICK_SEC)
st.rerun()

st.markdown("<div class='footerq'>Made by Quirlin</div>", unsafe_allow_html=True)
