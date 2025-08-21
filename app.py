import streamlit as st
import pandas as pd
import time
from datetime import datetime
import os

st.set_page_config(page_title="Quiz Night", page_icon="🎉")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
QUESTIONS_CSV = os.path.join(DATA_DIR, "questions.csv")
ANSWERS_CSV = os.path.join(DATA_DIR, "answers.csv")
RATINGS_CSV = os.path.join(DATA_DIR, "ratings.csv")

os.makedirs(DATA_DIR, exist_ok=True)

# Ensure CSV files exist
for path, cols in [
    (QUESTIONS_CSV, ["id","author","question","correct","wrong1","wrong2","wrong3","difficulty","created_at"]),
    (ANSWERS_CSV, ["timestamp","player","question_id","answer","is_correct"]),
    (RATINGS_CSV, ["timestamp","player","question_id","stars","comment"]),
]:
    if not os.path.exists(path):
        pd.DataFrame(columns=cols).to_csv(path, index=False)

@st.cache_data(ttl=2)
def load_df(path):
    return pd.read_csv(path)

def save_df(df, path):
    df.to_csv(path, index=False)

def refresh():
    st.cache_data.clear()

st.title("🎉 Quiz Night — collaborative questions & scoring")

tab_submit, tab_play, tab_rate, tab_scores, tab_manage = st.tabs(
    ["➕ Submit Question", "🎮 Play", "⭐ Rate Questions", "🏆 Scoreboard", "🧰 Manage"]
)

with tab_submit:
    st.header("Submit a new question")
    with st.form("new_q"):
        author = st.text_input("Your name (question author)", key="author").strip()
        question = st.text_area("Question text")
        correct = st.text_input("Correct answer")
        wrong1 = st.text_input("Wrong answer 1")
        wrong2 = st.text_input("Wrong answer 2 (optional)")
        wrong3 = st.text_input("Wrong answer 3 (optional)")
        difficulty = st.selectbox("Difficulty", ["Easy","Medium","Hard"], index=1)
        submitted = st.form_submit_button("Add question")
    if submitted:
        if not author or not question or not correct or not wrong1:
            st.error("Please provide author, question, correct answer, and at least one wrong answer.")
        else:
            qdf = load_df(QUESTIONS_CSV)
            qid = (qdf["id"].max()+1) if not qdf.empty else 1
            new_row = {
                "id": int(qid),
                "author": author,
                "question": question,
                "correct": correct,
                "wrong1": wrong1,
                "wrong2": wrong2,
                "wrong3": wrong3,
                "difficulty": difficulty,
                "created_at": datetime.utcnow().isoformat()
            }
            qdf = pd.concat([qdf, pd.DataFrame([new_row])], ignore_index=True)
            save_df(qdf, QUESTIONS_CSV)
            refresh()
            st.success(f"Added question #{qid}!")

with tab_play:
    st.header("Play Round")
    qdf = load_df(QUESTIONS_CSV)
    if qdf.empty:
        st.info("No questions yet. Go to 'Submit Question' first.")
    else:
        # Select a question to play
        q = qdf.sample(1).iloc[0] if st.toggle("Random question", value=True) else qdf.set_index("id").loc[st.number_input("Question ID", min_value=1, step=1)]
        st.subheader(f"Question #{int(q['id'])}")
        st.markdown(q["question"])

        # Assemble choices
        choices = [q["correct"], q["wrong1"]]
        if isinstance(q["wrong2"], str) and q["wrong2"].strip():
            choices.append(q["wrong2"])
        if isinstance(q["wrong3"], str) and q["wrong3"].strip():
            choices.append(q["wrong3"])
        import random
        random.shuffle(choices)

        with st.form(f"answer_{int(q['id'])}"):
            player = st.text_input("Your name (player)", key=f"player_{int(q['id'])}").strip()
            answer = st.radio("Choose your answer:", choices, captions=None)
            submitted = st.form_submit_button("Submit answer")
        if submitted:
            if not player:
                st.error("Please enter your player name.")
            else:
                is_correct = (answer == q["correct"])
                adf = load_df(ANSWERS_CSV)
                new_row = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "player": player,
                    "question_id": int(q["id"]),
                    "answer": answer,
                    "is_correct": bool(is_correct)
                }
                adf = pd.concat([adf, pd.DataFrame([new_row])], ignore_index=True)
                save_df(adf, ANSWERS_CSV)
                refresh()
                st.success("Answer recorded!")
                with st.expander("Reveal correct answer"):
                    st.markdown(f"✅ Correct answer: **{q['correct']}**")
                    st.caption(f"Author: {q['author']} | Difficulty: {q['difficulty']}")

with tab_rate:
    st.header("Rate questions (1–5 ⭐)")
    qdf = load_df(QUESTIONS_CSV)
    if qdf.empty:
        st.info("No questions to rate yet.")
    else:
        selected_id = st.selectbox("Pick a question to rate", qdf["id"].astype(int))
        q = qdf[qdf["id"] == selected_id].iloc[0]
        st.markdown(f"**Q#{int(q['id'])}:** {q['question']}  \nAuthor: {q['author']} | Difficulty: {q['difficulty']}")
        with st.form(f"rate_{int(q['id'])}"):
            player = st.text_input("Your name (rater)").strip()
            stars = st.slider("Stars", 1, 5, 4)
            comment = st.text_area("Comment (optional)")
            submitted = st.form_submit_button("Submit rating")
        if submitted:
            if not player:
                st.error("Please enter your name.")
            else:
                rdf = load_df(RATINGS_CSV)
                new_row = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "player": player,
                    "question_id": int(q["id"]),
                    "stars": int(stars),
                    "comment": comment
                }
                rdf = pd.concat([rdf, pd.DataFrame([new_row])], ignore_index=True)
                save_df(rdf, RATINGS_CSV)
                refresh()
                st.success("Rating recorded!")

with tab_scores:
    st.header("Scoreboard & Rules")
    qdf = load_df(QUESTIONS_CSV)
    adf = load_df(ANSWERS_CSV)
    rdf = load_df(RATINGS_CSV)

    st.markdown("""
**Points system (default):**
- Players: **+10** points for each correct answer.
- Authors: For each authored question, you get **5 × (N − C)** author points (N = total answers, C = number correct).
- If **C = 0** (nobody knew it): author only gets **2** points (we penalize impossible questions).
- If **C = N** (everyone knew it): author gets **0** points.
- Quality boost: Multiply the author points by the question's average stars scaled to **[0.6, 1.4]** (i.e., × (0.2×stars+0.4)).
- **Total score = player points + author points**.
    """)

    # Compute player points
    if not adf.empty:
        player_points = adf.groupby(["player"])["is_correct"].sum().mul(10).rename("player_points")
    else:
        player_points = pd.Series(dtype=float)

    # Compute author points per question
    author_points_rows = []
    if not qdf.empty:
        for _, row in qdf.iterrows():
            qid = int(row["id"])
            sub = adf[adf["question_id"] == qid]
            N = len(sub)
            C = int(sub["is_correct"].sum()) if N > 0 else 0
            base = 0
            if N > 0:
                base = 5 * max(N - C, 0)
                if C == 0:
                    base = 2
                elif C == N:
                    base = 0
            # rating multiplier
            rsub = rdf[rdf["question_id"] == qid]
            if len(rsub) > 0:
                stars_avg = rsub["stars"].mean()
            else:
                stars_avg = 3.0
            mult = 0.2 * stars_avg + 0.4  # 1 star -> 0.6x, 5 stars -> 1.4x
            points = base * mult
            author_points_rows.append({"author": row["author"], "question_id": qid, "author_points": points, "N": N, "C": C, "stars_avg": stars_avg})
    apdf = pd.DataFrame(author_points_rows)
    if not apdf.empty:
        author_totals = apdf.groupby("author")["author_points"].sum().rename("author_points")
    else:
        author_totals = pd.Series(dtype=float)

    # Merge totals
    total = pd.concat([player_points, author_totals], axis=1).fillna(0.0)
    total["total_score"] = total["player_points"] + total["author_points"]
    total = total.sort_values("total_score", ascending=False).reset_index().rename(columns={"index":"name"})

    st.subheader("Current ranking")
    if total.empty:
        st.info("No answers yet. Play a round!")
    else:
        st.dataframe(total.style.format({"player_points": "{:.0f}", "author_points": "{:.1f}", "total_score": "{:.1f}"}), use_container_width=True)

    with st.expander("Per-question stats"):
        if apdf.empty:
            st.caption("No question stats yet.")
        else:
            st.dataframe(apdf.sort_values("question_id"), use_container_width=True)

with tab_manage:
    st.header("Management & Utilities")
    st.markdown("Download or reset data files. Useful if you run multiple nights.")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Reset all data (keep questions)"):
            pd.DataFrame(columns=["timestamp","player","question_id","answer","is_correct"]).to_csv(ANSWERS_CSV, index=False)
            pd.DataFrame(columns=["timestamp","player","question_id","stars","comment"]).to_csv(RATINGS_CSV, index=False)
            st.success("Answers and ratings reset.")
            refresh()
    with col2:
        if st.button("🧹 Reset EVERYTHING (includes questions)"):
            pd.DataFrame(columns=["id","author","question","correct","wrong1","wrong2","wrong3","difficulty","created_at"]).to_csv(QUESTIONS_CSV, index=False)
            pd.DataFrame(columns=["timestamp","player","question_id","answer","is_correct"]).to_csv(ANSWERS_CSV, index=False)
            pd.DataFrame(columns=["timestamp","player","question_id","stars","comment"]).to_csv(RATINGS_CSV, index=False)
            st.success("All data reset.")
            refresh()

    st.download_button("⬇️ Download questions.csv", data=open(QUESTIONS_CSV, "rb").read(), file_name="questions.csv")
    st.download_button("⬇️ Download answers.csv", data=open(ANSWERS_CSV, "rb").read(), file_name="answers.csv")
    st.download_button("⬇️ Download ratings.csv", data=open(RATINGS_CSV, "rb").read(), file_name="ratings.csv")

st.caption("Built with ❤️ for your quiz night. Tip: run `streamlit run app.py` and share the local URL with friends on the same network.")
