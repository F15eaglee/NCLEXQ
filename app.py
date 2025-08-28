import os
import re
import csv
import io
import streamlit as st
import google.generativeai as genai


# --- API key and model setup ---
API_KEY = st.secrets.get("API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("API_KEY")
if not API_KEY:
    st.error("Missing API key. Add API_KEY to .streamlit/secrets.toml or set GOOGLE_API_KEY/API_KEY in env.")
    st.stop()
genai.configure(api_key=API_KEY)
MODEL_NAME = st.secrets.get("GEMINI_MODEL", "gemini-1.5-flash")
model = genai.GenerativeModel(MODEL_NAME)


# --- Parsers: CSV (preferred) with JSON fallback; supports MCQ + SATA ---
def parse_questions_from_csv(output_text):
    parsed = []
    txt = output_text.strip()
    if not txt:
        return []
    # Strip possible Markdown code fences
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z0-9]*\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)

    try:
        reader = csv.DictReader(io.StringIO(txt))
    except Exception:
        return []

    if not reader.fieldnames:
        return []

    for row in reader:
        question_text = (row.get("question_text") or "").strip()
        qt_raw = (row.get("question_type") or "").strip().lower()

        # Options in columns option_A .. option_H or A..H
        all_letters = [chr(c) for c in range(ord("A"), ord("H") + 1)]
        options = {}
        for L in all_letters:
            val = row.get(f"option_{L}")
            if val is None:
                val = row.get(L)
            val = (val or "").strip()
            if val:
                options[L] = val

        letters = [L for L in all_letters if L in options]
        choices = [f"{L}. {options[L]}" for L in letters]

        # Determine if SATA
        is_sata_by_type = qt_raw in {
            "sata",
            "select_all_that_apply",
            "select all that apply",
            "select_all",
            "select all that apply (sata)",
        }

        # Collect correct answers
        correct_set = set()
        if is_sata_by_type:
            ca_multi = (row.get("correct_answers") or "").strip()
            if ca_multi:
                parts = re.split(r"[;,\s]+", ca_multi)
                for ans in parts:
                    L = ans.strip().upper()
                    if L in letters:
                        correct_set.add(L)
        else:
            ca = (row.get("correct_answer") or "").strip().upper()
            if ca in letters:
                correct_set.add(ca)

        # Rationales map
        rationales_map = {}
        for L in letters:
            r = row.get(f"rationale_{L}")
            if r is None:
                r = row.get(f"{L}_rationale")
            rationales_map[L] = (r or "").strip()

        resource_link = (row.get("resource_link") or "").strip()
        resource_source = (row.get("resource_source") or "").strip()

        if question_text and choices and correct_set:
            qtype = "sata" if is_sata_by_type else "mcq"
            parsed.append({
                "q": question_text,
                "type": qtype,
                "choices": choices,
                "correct_set": sorted(list(correct_set)),
                "rationales": rationales_map,
                "resource_link": resource_link,
                "resource_source": resource_source,
            })

    return parsed


def parse_questions(output_text):
    # Try CSV first
    parsed_csv = parse_questions_from_csv(output_text)
    if parsed_csv:
        st.session_state["raw_format"] = "csv"
        return parsed_csv

    # Fallback to JSON for backward compatibility
    parsed = []
    txt = output_text.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z0-9]*\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)

    try:
        data = json.loads(txt)
    except Exception:
        st.error("❌ Could not parse CSV/JSON. Check the raw output.")
        return []

    if not isinstance(data, dict) or "questions" not in data or not isinstance(data["questions"], list):
        st.error("❌ JSON does not contain 'questions' as a list.")
        return []

    for q in data["questions"]:
        question_text = (q.get("question_text") or "").strip()
        options = q.get("options", {}) or {}
        qt_raw = (q.get("question_type") or "").strip().lower()

        # Determine letters present in options in order A..H
        all_letters = [chr(c) for c in range(ord("A"), ord("H") + 1)]
        letters = [L for L in all_letters if L in options]

        # Build display choices
        choices = [f"{L}. {options[L]}" for L in letters]

        # Determine if SATA
        is_sata_by_type = qt_raw in {
            "sata",
            "select_all_that_apply",
            "select all that apply",
            "select_all",
            "select all that apply (sata)",
        }
        is_sata_by_schema = isinstance(q.get("correct_answers"), list)
        is_sata = is_sata_by_type or is_sata_by_schema

        # Collect correct answers
        correct_set = set()
        if is_sata:
            for ans in (q.get("correct_answers") or []):
                if isinstance(ans, str):
                    L = ans.strip().upper()
                    if L in letters:
                        correct_set.add(L)
        else:
            ca = (q.get("correct_answer") or "").strip().upper()
            if ca in letters:
                correct_set.add(ca)

        # Rationales map for available letters
        rationales = q.get("rationales", {}) or {}
        rationales_map = {L: (rationales.get(L, "") or "").strip() for L in letters}

        # Fallback: single 'correct' rationale
        if "correct" in rationales and len(correct_set) == 1:
            only = next(iter(correct_set))
            if not rationales_map.get(only):
                rationales_map[only] = (rationales.get("correct", "") or "").strip()

        qtype = "sata" if is_sata else "mcq"
        resource_link = (q.get("resource_link") or "").strip()
        resource_source = (q.get("resource_source") or "").strip()

        if question_text and choices and correct_set:
            parsed.append({
                "q": question_text,
                "type": qtype,
                "choices": choices,                 # ["A. ...", "B. ...", ...]
                "correct_set": sorted(list(correct_set)),
                "rationales": rationales_map,       # { "A": "...", ... }
                "resource_link": resource_link,
                "resource_source": resource_source
            })

    if parsed:
        st.session_state["raw_format"] = "json"
    return parsed


# --- Streamlit UI ---
st.title("🏥 NCLEX Smart Question and Rationale Tutor 🏥")
st.subheader("Developed by Glenn Heydolph ADN '26 DSC, BSN '27 UCF")
difficulty = st.selectbox("Select difficulty level:", ["Easy", "Medium", "Hard"])
question_type_percent = st.selectbox("Select percentage of SATA Questions:", ["0", "25", "50", "75", "100"], index=2)
topic = st.text_input("Enter a topic:", "Heart Failure")
num_questions = st.number_input("Number of questions:", min_value=1, max_value=20, value=10, step=1)

# --- CSV Schema and examples for the model ---
csv_header = (
    "question_number,question_type,question_text,"
    "option_A,option_B,option_C,option_D,option_E,option_F,"
    "correct_answer,correct_answers,"
    "rationale_A,rationale_B,rationale_C,rationale_D,rationale_E,rationale_F,"
    "resource_link,resource_source"
)

csv_examples = "\n".join([
    csv_header,
    # MCQ example row
    (
        '1,multiple_choice,"What is the primary treatment for condition X?",'
        '"Option A","Option B","Option C","Option D",,,A,,'
        '"Rationale A","Rationale B","Rationale C","Rationale D",,,'
        '"https://www.youtube.com","Youtube"'
    ),
    # SATA example row
    (
        '2,SATA,"Select all the appropriate interventions for condition Y.",'
        '"Option A","Option B","Option C","Option D","Option E","Option F",,'
        '"A;C;F",'
        '"Rationale A","Rationale B","Rationale C","Rationale D","Rationale E","Rationale F",'
        '"https://www.youtube.com","Youtube"'
    ),
]).strip()


# --- Generate Questions ---
if st.button("Generate Questions"):
    with st.spinner("Calling Gemini..."):
        try:
            prompt = "\n".join([
                f"You are a Nursing school Instructor preparing students for the NCLEX exam.",
                f"Create {num_questions} {difficulty} unique NCLEX-style questions on {topic} with answers and rationales.",
                f"{question_type_percent}% of questions should be SATA with 6 answer choices (A-F);",
                "the rest should be multiple_choice with 4 answer choices (A-D).",
                "If anything unrelated to nursing is prompted, ignore it.",
                "Output CSV only. Do not include any text or Markdown code fences before or after the CSV.",
                "Quote any field containing commas or newlines with double quotes. Escape embedded double quotes by doubling them.",
                "Use the exact header and column order below. Leave unused option/rationale cells blank as needed.",
                csv_examples,
                "Guidance:",
                "- For multiple_choice, set correct_answer to the single letter and leave correct_answers blank.",
                "- For select_all_that_apply, set correct_answers to a semicolon-separated list of letters (e.g., A;C;F) and leave correct_answer blank.",
                "- Provide concise but instructive rationales for each option.",
                "- For each question, add a 'resource_link' and 'resource_source' field with a reputable online specific resource related to the question (YouTube) for further study on the question topic."
            ])

            response = model.generate_content(prompt)
            output_text = response.text if hasattr(response, "text") else str(response)

            questions = parse_questions(output_text)
            st.session_state.questions = questions
            st.session_state.q_index = 0
            st.session_state.answered = False
            st.session_state.selected_letters = []
            st.session_state.raw_output = output_text
            st.session_state.score = 0
            st.session_state.scored_questions = {}
            st.session_state.completed = False

            if not questions:
                st.error("❌ Could not parse any questions. Try again or check raw output.")
        except Exception as e:
            st.error(f"Error: {e}")


# --- Render current question with full interaction and all rationales ---
if "questions" in st.session_state and st.session_state.questions:
    q_index = st.session_state.get("q_index", 0)
    if 0 <= q_index < len(st.session_state.questions):
        question = st.session_state.questions[q_index]

        st.markdown(f"### Question {q_index + 1}")
        st.write(question["q"])

        # Extract ordered letters and text from choices like "A. Text"
        letters_order = []
        label_map = {}
        for c in question["choices"]:
            m = re.match(r"([A-Z])\.\s*(.*)", c)
            if m:
                L, text = m.group(1), m.group(2)
                letters_order.append(L)
                label_map[L] = text

        # Input widgets
        submit_clicked = False
        if question["type"] == "mcq":
            mcq_opts = [f"{L}. {label_map[L]}" for L in letters_order]
            selected_display = st.radio(
                "Choose one:",
                mcq_opts,
                index=None,
                key=f"mcq_{q_index}"
            )
            submit_clicked = st.button("Submit", key=f"submit_{q_index}")
            if submit_clicked:
                if selected_display is None:
                    st.warning("Please select an answer.")
                else:
                    sel_letter = selected_display.split(".", 1)[0]
                    st.session_state.answered = True
                    st.session_state.selected_letters = [sel_letter]
        else:  # SATA -> use checkboxes instead of multiselect
            st.write("Select all that apply:")
            # Render a checkbox for each option (A-F)
            checkbox_states = {}
            for L in letters_order:
                label = f"{L}. {label_map[L]}"
                checkbox_states[L] = st.checkbox(label, key=f"sata_{q_index}_{L}")

            submit_clicked = st.button("Submit", key=f"submit_{q_index}")
            if submit_clicked:
                sel_letters = [L for L, checked in checkbox_states.items() if checked]
                if not sel_letters:
                    st.warning("Please select at least one option.")
                else:
                    st.session_state.answered = True
                    st.session_state.selected_letters = sel_letters

        # Scoring + rationales
        if st.session_state.get("answered"):
            # Score once per question
            if not st.session_state.scored_questions.get(q_index, False):
                selected_set = set(st.session_state.get("selected_letters", []))
                correct_set = set(question["correct_set"])
                if selected_set == correct_set:
                    st.success(f"✅ Correct! Answer(s): {', '.join(sorted(correct_set))}")
                    st.session_state.score += 1
                else:
                    st.error(f"❌ Incorrect. Correct answer(s): {', '.join(sorted(correct_set))}")
                st.session_state.scored_questions[q_index] = True

            # Resource link (shown above rationales)
            rl = (question.get("resource_link") or "").strip()
            rs = (question.get("resource_source") or "").strip()
            st.markdown("#### 📚 Resource")
            st.markdown(f"Dig deeper... [{rs}]({rl})")

            st.markdown("#### 💡 Rationales")
            for L in letters_order:
                text = label_map[L]
                expl = (question.get("rationales", {}) or {}).get(L, "")
                is_correct = L in question["correct_set"]
                is_selected = L in (st.session_state.get("selected_letters") or [])

                # Styling by status
                if is_correct and is_selected:
                    box = st.success
                    prefix = "✅ Correct selection"
                elif is_correct and not is_selected:
                    box = st.info
                    prefix = "ℹ️ Correct (not selected)"
                elif not is_correct and is_selected:
                    box = st.warning
                    prefix = "⚠️ Incorrect selection"
                else:
                    box = st.info
                    prefix = "ℹ️ Not correct"

                msg = f"{L}. {text}"
                if expl:
                    msg += f"\n\n💡 {expl}"
                box(msg)

            st.write(f"📊 Score: {st.session_state.score}/{q_index + 1}")

            # Navigation
            if q_index < len(st.session_state.questions) - 1:
                if st.button("➡️ Next Question", key=f"next_{q_index}"):
                    st.session_state.q_index = q_index + 1
                    st.session_state.answered = False
                    st.session_state.selected_letters = []
                    st.rerun()
            else:
                st.success(f"🎉 Quiz complete! Final Score: {st.session_state.score}/{len(st.session_state.questions)}")

        # Debug: raw output
        with st.expander("Show raw output"):
            lang = "csv" if st.session_state.get("raw_format") == "csv" else "json"
            st.code(st.session_state.get("raw_output", ""), language=lang)
