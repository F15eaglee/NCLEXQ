import os
import re
import json
import csv
import io
import urllib.parse as urlparse
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
        reader = csv.DictReader(io.StringIO(txt), restkey="_rest", skipinitialspace=True)
    except Exception:
        return []

    if not reader.fieldnames:
        return []

    # Helpers for cleaning/recognizing text and URLs
    def _clean_text(s: str) -> str:
        return (s or "").strip().strip('"').strip()

    def _looks_like_url(s: str) -> bool:
        return bool(re.match(r"^(https?://|www\.)", (s or "").strip(), re.IGNORECASE))

    for row in reader:
        # Support both old "question_text" and new lowercase "question" column
        question_text = (row.get("question") or row.get("question_text") or "").strip()
        qt_raw = (row.get("question_type") or "").strip().lower()

        # Options in columns option_a..option_f (lowercase) and option_A..option_H or A..H
        all_letters = [chr(c) for c in range(ord("A"), ord("H") + 1)]
        options = {}
        for L in all_letters:
            # Accept option_A, option_a, A, a
            val = (
                row.get(f"option_{L}")
                or row.get(f"option_{L.lower()}")
                or row.get(L)
                or row.get(L.lower())
            )
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
        ca_multi_raw = (row.get("correct_answers") or "").strip()
        ca_single_raw = (row.get("correct_answer") or "").strip()

        if is_sata_by_type:
            if ca_multi_raw:
                # Prefer semicolon per spec, but accept commas/whitespace too
                parts = re.split(r"[;,\s]+", ca_multi_raw)
                for ans in parts:
                    L = ans.strip().upper()
                    if L in letters:
                        correct_set.add(L)
        else:
            ca = ca_single_raw.upper()
            if ca in letters:
                correct_set.add(ca)

        # Rationales map (with MCQ misalignment correction if needed)
        def _get_rationale_cell(letter: str) -> str:
            return (
                row.get(f"rationale_{letter}")
                or row.get(f"rationale_{letter.lower()}")
                or row.get(f"{letter}_rationale")
                or ""
            )

        # Base rationales from row
        base_rats = {L: (_get_rationale_cell(L) or "").strip() for L in all_letters}

        # Detect misalignment for MCQ where correct_answers accidentally contains rationale text
        def _looks_like_letter_list(s: str) -> bool:
            return bool(re.fullmatch(r"\s*[A-Ha-h](\s*[;,]\s*[A-Ha-h])*\s*", s or ""))

        rationales_map = {}
        youtube_term_from_shift = ""
        if not is_sata_by_type and ca_single_raw and ca_multi_raw and not _looks_like_letter_list(ca_multi_raw):
            # Shift: correct_answers -> rationale_A; rationale_A->B; ...; rationale_E->F; rationale_F -> youtube term (if missing)
            shift_chain = {
                "A": ca_multi_raw,
                "B": base_rats.get("A", ""),
                "C": base_rats.get("B", ""),
                "D": base_rats.get("C", ""),
                "E": base_rats.get("D", ""),
                "F": base_rats.get("E", ""),
            }
            for L in letters:
                rationales_map[L] = (shift_chain.get(L, base_rats.get(L, "")) or "").strip()
            youtube_term_from_shift = (base_rats.get("F", "") or "").strip()
        else:
            for L in letters:
                rationales_map[L] = base_rats.get(L, "")

        # Optional YouTube search term (robust extraction)
        # Prefer new youtube_search_term, fallback to older names
        search_term = _clean_text(row.get("youtube_search_term"))
        if not search_term:
            search_term = _clean_text(row.get("search_term"))
        if not search_term:
            # Try alternate header names
            for alt in ("search term", "search", "youtube_search", "youtube search"):
                search_term = _clean_text(row.get(alt))
                if search_term:
                    break
        if not search_term:
            # Look into any extra trailing columns for a plausible term (last non-empty, non-URL)
            extras = row.get("_rest") or []
            for val in reversed(extras):
                candidate = _clean_text(val)
                if candidate and not _looks_like_url(candidate):
                    search_term = candidate
                    break
        # If we detected MCQ shift and youtube term still empty, backfill from shifted rationale_F value
        if not search_term and youtube_term_from_shift:
            search_term = _clean_text(youtube_term_from_shift)

        if question_text and choices and correct_set:
            qtype = "sata" if is_sata_by_type else "mcq"
            parsed.append({
                "q": question_text,
                "type": qtype,
                "choices": choices,
                "correct_set": sorted(list(correct_set)),
                "rationales": rationales_map,
                "search_term": search_term,
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

        if question_text and choices and correct_set:
            parsed.append({
                "q": question_text,
                "type": qtype,
                "choices": choices,                 # ["A. ...", "B. ...", ...]
                "correct_set": sorted(list(correct_set)),
                "rationales": rationales_map,      # { "A": "...", ... }
                "search_term": (q.get("search_term") or "").strip().strip('"').strip()
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
    "search_term"
)

csv_examples = "\n".join([
    csv_header,
    # MCQ example row
    (
        '1,multiple_choice,"What is the primary treatment for condition X?",'
        '"Option A","Option B","Option C","Option D",,,A,,'
        '"Rationale A","Rationale B","Rationale C","Rationale D",,,'
        '"heart failure treatment nursing"'
    ),
    # SATA example row
    (
        '2,SATA,"Select all the appropriate interventions for condition Y.",'
        '"Option A","Option B","Option C","Option D","Option E","Option F",,'
        '"A;C;F",'
        '"Rationale A","Rationale B","Rationale C","Rationale D","Rationale E","Rationale F",'
        '"heart failure patient teaching nursing"'
    ),
]).strip()


# --- Generate Questions ---
if st.button("Generate Questions"):
    with st.spinner("Calling Gemini 2.5 fast..."):
        try:
            # Build improved prompt with updated CSV schema and examples
            csv_examples = "\n".join([
                "question_type,question,option_a,option_b,option_c,option_d,option_e,option_f,correct_answer,correct_answers,rationale_a,rationale_b,rationale_c,rationale_d,rationale_e,rationale_f,youtube_search_term",
                'multiple_choice,"Which action takes priority for a client with acute pulmonary edema?","Administer oxygen","Encourage oral fluids","Obtain daily weight","Teach low-sodium diet",,A,"Improves oxygenation immediately","Fluids may worsen overload","Weight is monitoring, not priority","Teaching is not priority in acute event",,,"pulmonary edema"',
                'SATA,"Select all initial nursing actions for suspected hypoglycemia.","Check blood glucose","Give long-acting insulin","Provide 15 g fast carbs","Reassess in 15 min","Call rapid response if LOC declines","Start IV access",,"A;C;F","Confirms diagnosis","Contraindicated; will worsen","Raises glucose quickly","Ensures treatment worked","Escalate if worsening","Allows dextrose/med access","hypoglycemia care"',
            ])

            improved_prompt = f"""
            # ROLE & GOAL
            You are an expert NCLEX Test Development Specialist and a seasoned Nursing Educator. Your goal is to generate high-quality, unique practice questions that rigorously test a nursing student's clinical judgment and readiness for the NCLEX exam.

            # CORE TASK
            Generate exactly {num_questions} NCLEX-style practice questions on the topic of "{topic}". The questions should be of {difficulty} difficulty.

            # QUESTION SPECIFICATIONS
            1.  **Question Mix**:
                * Exactly {question_type_percent}% of the questions must be **Select All That Apply (SATA)**.
                * The remaining questions must be standard **Multiple-Choice (MCQ)**.
            2.  **Content Quality**:
                * Questions must focus on application, analysis, and evaluation—not simple recall.
                * Scenarios should be realistic and reflect common clinical situations.
                * Incorrect answer choices (distractors) must be plausible and educationally valuable.
            3.  **Answer Choices**:
                * MCQ questions must have exactly 4 choices (A, B, C, D).
                * SATA questions must have exactly 6 choices (A, B, C, D, E, F).

            # OUTPUT FORMATTING
            1.  **Strictly CSV Output**: Your entire response MUST be a valid CSV file. Do NOT include any introductory text, explanations, or markdown code fences (like ```csv) before or after the CSV data.
            2.  **CSV Header**: The CSV data must start with the following exact header row. The column order is critical.
                `question_type,question,option_a,option_b,option_c,option_d,option_e,option_f,correct_answer,correct_answers,rationale_a,rationale_b,rationale_c,rationale_d,rationale_e,rationale_f,youtube_search_term`
            3.  **Content Logic**:
                * **Rationales**: Provide a concise and instructive rationale for **every** answer choice (both correct and incorrect).
                * **YouTube Search Term**: For each question, provide a simple, 2-3 word search term for finding a relevant educational video that is related to the question and answer.
                * **Correct Answer Columns (CRITICAL)**:
                    * For **MCQ** questions: Put the single correct letter in the `correct_answer` column. The `correct_answers` column MUST be blank.
                    * For **SATA** questions: Put the semicolon-separated list of correct letters (e.g., A;C;F) in the `correct_answers` column. The `correct_answer` column MUST be blank.

            # GUARDRAIL
            If the topic "{topic}" is not a recognized nursing or medical subject, respond with ONLY the CSV header line and nothing else.

            # EXAMPLES
            Follow the formatting and logic demonstrated in these examples precisely.
            {csv_examples}
            """
            prompt = improved_prompt

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
            st.session_state.expected_count = num_questions

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

            # YouTube search helper (uses provided field only)
            search_term = (question.get("search_term") or "").strip()
            if search_term:
                yt_url = f"https://www.youtube.com/results?search_query=Nursing {urlparse.quote(search_term)}"
                st.markdown("#### 🔎 Find related videos")
                # Short caption: use at most 60 chars
                short_caption = search_term if len(search_term) <= 60 else (search_term[:57] + "...")
                st.caption(short_caption)
                st.link_button("Search on YouTube", yt_url, help="Opens YouTube results in a new tab")

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
                if st.button("Next Question", key=f"next_{q_index}"):
                    st.session_state.q_index = q_index + 1
                    st.session_state.answered = False
                    st.session_state.selected_letters = []
                    st.rerun()
            else:
                st.success(f"🎉 Quiz complete! Final Score: {st.session_state.score}/{len(st.session_state.questions)}")

        # Bottom fallback navigation (in case state glitches)
        if q_index < len(st.session_state.questions) - 1 and st.session_state.scored_questions.get(q_index, False):
            if st.button("Next Question", key=f"next_bottom_{q_index}"):
                st.session_state.q_index = q_index + 1
                st.session_state.answered = False
                st.session_state.selected_letters = []
                st.rerun()

        # If fewer questions than requested were generated, offer to generate the missing ones
        total_q = len(st.session_state.questions)
        expected_q = st.session_state.get("expected_count", total_q)
        if total_q < expected_q:
            missing_q = expected_q - total_q
            st.warning(f"Only {total_q}/{expected_q} questions generated. {missing_q} missing.")
            if st.button(f"Generate {missing_q} more", key="gen_more"):
                try:
                    # Reuse the same improved prompt pattern with the missing count
                    csv_examples = "\n".join([
                        "question_type,question,option_a,option_b,option_c,option_d,option_e,option_f,correct_answer,correct_answers,rationale_a,rationale_b,rationale_c,rationale_d,rationale_e,rationale_f,youtube_search_term",
                        'multiple_choice,"Which action takes priority for a client with acute pulmonary edema?","Administer oxygen","Encourage oral fluids","Obtain daily weight","Teach low-sodium diet",,A,"Improves oxygenation immediately","Fluids may worsen overload","Weight is monitoring, not priority","Teaching is not priority in acute event",,,"pulmonary edema"',
                        'SATA,"Select all initial nursing actions for suspected hypoglycemia.","Check blood glucose","Give long-acting insulin","Provide 15 g fast carbs","Reassess in 15 min","Call rapid response if LOC declines","Start IV access",,"A;C;F","Confirms diagnosis","Contraindicated; will worsen","Raises glucose quickly","Ensures treatment worked","Escalate if worsening","Allows dextrose/med access","hypoglycemia care"',
                    ])

                    improved_prompt_more = f"""
                    # ROLE & GOAL
                    You are an expert NCLEX Test Development Specialist and a seasoned Nursing Educator. Your goal is to generate high-quality, unique practice questions that rigorously test a nursing student's clinical judgment and readiness for the NCLEX exam.

                    # CORE TASK
                    Generate exactly {missing_q} NCLEX-style practice questions on the topic of "{topic}". The questions should be of {difficulty} difficulty.

                    # QUESTION SPECIFICATIONS
                    1.  **Question Mix**:
                        * Exactly {question_type_percent}% of the questions must be **Select All That Apply (SATA)**.
                        * The remaining questions must be standard **Multiple-Choice (MCQ)**.
                    2.  **Content Quality**:
                        * Questions must focus on application, analysis, and evaluation—not simple recall.
                        * Scenarios should be realistic and reflect common clinical situations.
                        * Incorrect answer choices (distractors) must be plausible and educationally valuable.
                    3.  **Answer Choices**:
                        * MCQ questions must have exactly 4 choices (A, B, C, D).
                        * SATA questions must have exactly 6 choices (A, B, C, D, E, F).

                    # OUTPUT FORMATTING
                    1.  **Strictly CSV Output**: Your entire response MUST be a valid CSV file. Do NOT include any introductory text, explanations, or markdown code fences before or after the CSV data.
                    2.  **CSV Header**: Use this exact header row and column order:
                        `question_type,question,option_a,option_b,option_c,option_d,option_e,option_f,correct_answer,correct_answers,rationale_a,rationale_b,rationale_c,rationale_d,rationale_e,rationale_f,youtube_search_term`
                    3.  **Content Logic**:
                        * Provide a concise rationale for every answer choice.
                        * Provide a short YouTube search term related to the question and answer.
                        * For MCQ: use `correct_answer` only; leave `correct_answers` blank.
                        * For SATA: use `correct_answers` with semicolons; leave `correct_answer` blank.

                    # GUARDRAIL
                    If the topic "{topic}" is not a recognized nursing or medical subject, respond with ONLY the CSV header line and nothing else.

                    # EXAMPLES
                    Follow the formatting and logic demonstrated in these examples precisely.
                    {csv_examples}
                    """

                    resp_more = model.generate_content(improved_prompt_more)
                    out_more = resp_more.text if hasattr(resp_more, "text") else str(resp_more)
                    new_qs = parse_questions(out_more)
                    if new_qs:
                        st.session_state.questions.extend(new_qs)
                        st.success(f"Added {len(new_qs)} more question(s).")
                        st.rerun()
                    else:
                        st.error("Could not parse additional questions. Check raw output.")
                        st.session_state.raw_output = out_more
                except Exception as e:
                    st.error(f"Error generating more: {e}")

        # Debug: raw output
        with st.expander("Show raw output"):
            lang = "csv" if st.session_state.get("raw_format") == "csv" else "json"
            st.code(st.session_state.get("raw_output", ""), language=lang)
else:
    # Even if parsing failed, expose the raw output for debugging
    raw = st.session_state.get("raw_output", "")
    if raw:
        st.markdown("### Debug: raw model output")
        # Heuristic to guess language for code highlighting
        guess_lang = "json" if raw.strip().startswith("{") else "csv"
        with st.expander("Show raw output"):
            st.code(raw, language=guess_lang)
            st.download_button("Download raw output", raw, file_name="raw_output.txt")
