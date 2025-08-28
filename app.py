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
MODEL_NAME = st.secrets.get("GEMINI_MODEL", "gemini-2.5-flash")
model = genai.GenerativeModel(MODEL_NAME)

# --- Optimized Parsing Functions ---
@st.cache_data
def parse_questions_from_csv(output_text: str) -> tuple[list, str]:
    """Parse CSV output into structured questions with detailed error reporting."""
    if not output_text.strip():
        return [], "Empty input text."

    # Clean text
    txt = (
        output_text.strip()
        .replace("\ufeff", "")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\xa0", " ")
    )
    txt = re.sub(r"^```[a-zA-Z0-9\s]*\n|\n```$", "", txt)

    # Find header
    expected_header = (
        "question_type,question,option_a,option_b,option_c,option_d,option_e,option_f,"
        "correct_answer,correct_answers,rationale_a,rationale_b,rationale_c,rationale_d,"
        "rationale_e,rationale_f,youtube_search_term"
    ).split(",")
    lines = txt.splitlines()
    header_idx = next((i for i, line in enumerate(lines) if re.match(r"^\s*question_type\s*,", line, re.IGNORECASE)), -1)
    if header_idx == -1:
        return [], "No valid CSV header found."

    txt = "\n".join(lines[header_idx:])
    try:
        raw_rows = list(csv.reader(io.StringIO(txt), skipinitialspace=True, quoting=csv.QUOTE_ALL))
        if not raw_rows:
            return [], "No rows in CSV."
    except csv.Error as e:
        return [], f"CSV parsing error: {str(e)}"

    header_lower = [h.strip().lower().lstrip("\ufeff") for h in raw_rows[0]]
    if not all(h in header_lower for h in ["question_type", "question", "correct_answer", "correct_answers"]):
        return [], "Incomplete or mismatched CSV header."

    norm_rows = []
    incomplete_rows = []
    for row_idx, row in enumerate(raw_rows[1:], 1):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) < len(header_lower):
            incomplete_rows.append(f"Row {row_idx}: Incomplete row, only {len(row)} columns")
            continue
        cells = row[:len(header_lower)]
        norm_rows.append(dict(zip(header_lower, cells)))

    def _clean_text(s: str) -> str:
        return s.strip().strip('"').strip()

    parsed = []
    all_letters = list("ABCDEF")
    skipped_reasons = []
    for row_idx, row in enumerate(norm_rows, 1):
        question_text = _clean_text(row.get("question", ""))
        if not question_text:
            skipped_reasons.append(f"Row {row_idx}: Missing question text")
            continue

        qt_raw = row.get("question_type", "").lower()
        is_sata = qt_raw in {"sata", "select_all_that_apply", "select all that apply", "select_all", "select all that apply (sata)"}

        # Extract options
        options = {L: _clean_text(row.get(f"option_{L.lower()}")) for L in all_letters if _clean_text(row.get(f"option_{L.lower()}"))}
        letters = sorted(options.keys())
        choices = [f"{L}. {options[L]}" for L in letters]
        min_options = 6 if is_sata else 4
        if len(letters) < min_options:
            skipped_reasons.append(f"Row {row_idx}: {qt_raw.upper()} requires {min_options} options, found {len(letters)}")
            continue

        # Handle correct answers
        correct_set = set()
        ca_multi = _clean_text(row.get("correct_answers", ""))
        ca_single = _clean_text(row.get("correct_answer", ""))
        if is_sata and ca_multi:
            correct_set = {L.upper() for L in re.split(r"[;,\s]+", ca_multi) if L.upper() in letters and L.strip()}
        elif ca_single:
            ca = ca_single.upper()
            if ca in letters:
                correct_set.add(ca)
            else:
                skipped_reasons.append(f"Row {row_idx}: Invalid correct_answer '{ca_single}', not in {letters}")
                continue
        else:
            skipped_reasons.append(f"Row {row_idx}: No valid correct_answer or correct_answers")
            continue

        # Extract rationales
        rationales_map = {L: _clean_text(row.get(f"rationale_{L.lower()}")) for L in letters}

        # Extract YouTube search term
        search_term = _clean_text(row.get("youtube_search_term", ""))

        if question_text and choices and correct_set:
            parsed.append({
                "q": question_text,
                "type": "sata" if is_sata else "mcq",
                "choices": choices,
                "correct_set": sorted(list(correct_set)),
                "rationales": rationales_map,
                "search_term": search_term,
            })
        else:
            skipped_reasons.append(f"Row {row_idx}: Missing question_text, choices, or correct answers")

    error_msg = "Parsed successfully" if parsed else "No valid questions parsed."
    if incomplete_rows:
        error_msg += f"\nIncomplete rows detected: {'; '.join(incomplete_rows)}"
    if skipped_reasons:
        error_msg += f"\nSkipped rows: {'; '.join(skipped_reasons)}"
    return parsed, error_msg

@st.cache_data
def parse_questions(output_text: str) -> tuple[list, str]:
    """Parse output, preferring CSV, with JSON fallback."""
    parsed, error_msg = parse_questions_from_csv(output_text)
    if parsed:
        st.session_state["raw_format"] = "csv"
        return parsed, error_msg

    txt = re.sub(r"^```[a-zA-Z0-9\s]*\n|\n```$", "", output_text.strip())
    try:
        data = json.loads(txt)
        if not isinstance(data, dict) or "questions" not in data:
            return [], "JSON does not contain 'questions' list."
    except json.JSONDecodeError:
        return [], "Invalid JSON format."

    parsed = []
    all_letters = list("ABCDEF")
    skipped_reasons = []
    for q_idx, q in enumerate(data.get("questions", []), 1):
        question_text = q.get("question_text", "").strip()
        options = q.get("options", {})
        qt_raw = q.get("question_type", "").lower()
        is_sata = qt_raw in {"sata", "select_all_that_apply", "select all that apply", "select_all", "select all that apply (sata)"} or isinstance(q.get("correct_answers"), list)
        
        letters = [L for L in all_letters if L in options]
        choices = [f"{L}. {options[L]}" for L in letters]
        min_options = 6 if is_sata else 4
        if len(letters) < min_options:
            skipped_reasons.append(f"Question {q_idx}: {qt_raw.upper()} requires {min_options} options, found {len(letters)}")
            continue

        correct_set = set()
        if is_sata:
            for ans in q.get("correct_answers", []):
                L = ans.strip().upper()
                if L in letters:
                    correct_set.add(L)
            if not correct_set:
                skipped_reasons.append(f"Question {q_idx}: No valid correct_answers")
                continue
        else:
            ca = q.get("correct_answer", "").upper()
            if ca in letters:
                correct_set.add(ca)
            else:
                skipped_reasons.append(f"Question {q_idx}: Invalid correct_answer '{ca}'")
                continue

        rationales_map = {L: q.get("rationales", {}).get(L, "") for L in letters}
        if "correct" in q.get("rationales", {}) and len(correct_set) == 1:
            rationales_map[next(iter(correct_set))] = q["rationales"].get("correct", "")

        if question_text and choices and correct_set:
            parsed.append({
                "q": question_text,
                "type": "sata" if is_sata else "mcq",
                "choices": choices,
                "correct_set": sorted(list(correct_set)),
                "rationales": rationales_map,
                "search_term": q.get("search_term", "").strip(),
            })
        else:
            skipped_reasons.append(f"Question {q_idx}: Missing question_text, choices, or correct answers")

    error_msg = "Parsed successfully" if parsed else "No valid questions parsed from JSON."
    if skipped_reasons:
        error_msg += f"\nSkipped questions: {'; '.join(skipped_reasons)}"
    if parsed:
        st.session_state["raw_format"] = "json"
    return parsed, error_msg

# --- Streamlit UI with Form ---
st.title("🏥 NCLEX Smart Question and Rationale Tutor 🏥")
st.subheader("Developed by Glenn Heydolph ADN '26 DSC, BSN '27 UCF")

with st.form("quiz_settings"):
    difficulty = st.selectbox("Select difficulty level:", ["Easy", "Medium", "Hard"])
    question_type_percent = st.selectbox("Select percentage of SATA Questions:", ["0", "25", "50", "75", "100"], index=2)
    topic = st.text_input("Enter a topic:", "Heart Failure")
    num_questions = st.number_input("Number of questions:", min_value=1, max_value=20, value=2, step=1)
    submitted = st.form_submit_button("Generate Questions")

# --- Generate Questions ---
@st.cache_data
def generate_questions(topic: str, difficulty: str, num_questions: int, question_type_percent: str) -> str:
    """Cache the model response to avoid redundant API calls."""
    csv_examples = "\n".join([
        "question_type,question,option_a,option_b,option_c,option_d,option_e,option_f,correct_answer,correct_answers,rationale_a,rationale_b,rationale_c,rationale_d,rationale_e,rationale_f,youtube_search_term",
        'multiple_choice,"Which action takes priority for a client with acute pulmonary edema?","Administer oxygen","Encourage oral fluids","Obtain daily weight","Teach low-sodium diet",,,A,,"Improves oxygenation immediately","Fluids may worsen overload","Weight is monitoring, not priority","Teaching is not priority in acute event",,,"pulmonary edema"',
        'SATA,"Select all initial nursing actions for suspected hypoglycemia.","Check blood glucose","Give long-acting insulin","Provide 15 g fast carbs","Reassess in 15 min","Call rapid response if LOC declines","Start IV access",,,"A;C;F","Confirms diagnosis","Contraindicated; will worsen","Raises glucose quickly","Ensures treatment worked","Escalate if worsening","Allows dextrose/med access","hypoglycemia care"',
    ])

    prompt = f"""
    # ROLE
    You are an NCLEX Test Development Specialist. Generate high-quality NCLEX-style practice questions.

    # TASK
    Generate exactly {num_questions} questions on "{topic}" at {difficulty} difficulty.

    # SPECIFICATIONS
    - {question_type_percent}% SATA, rest MCQ.
    - Focus on application, analysis, evaluation.
    - MCQ: 4 choices (A-D). SATA: 6 choices (A-F).
    - Provide concise rationales for all choices.
    - Include a 2-3 word YouTube search term per question.
    - Ensure complete rows; do not truncate output.

    # OUTPUT
    - CSV only, no extra text, explanations, or code fences (e.g., ```csv).
    - Header: {','.join(csv_examples.splitlines()[0].split(','))}
    - MCQ: correct_answer (single letter A-D), correct_answers (blank).
    - SATA: correct_answers (semicolon-separated, e.g., A;C;F), correct_answer (blank).
    - For non-nursing topics, return only the header.
    - Quote all fields to handle commas and ensure complete rows.

    # EXAMPLES
    {csv_examples}
    """
    try:
        response = model.generate_content(
            prompt,
            generation_config={"max_output_tokens": 4096}  # Increase token limit to avoid truncation
        )
        return response.text if hasattr(response, "text") else str(response)
    except Exception as e:
        return f"Error: {str(e)}"

if submitted:
    with st.spinner("Generating questions..."):
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                output_text = generate_questions(topic, difficulty, num_questions, question_type_percent)
                questions, parse_error = parse_questions(output_text)
                if questions:
                    st.session_state.questions = questions
                    st.session_state.q_index = 0
                    st.session_state.answered = False
                    st.session_state.selected_letters = []
                    st.session_state.score = 0
                    st.session_state.scored_questions = {}
                    st.session_state.raw_output = output_text[:1000]
                    st.session_state.expected_count = num_questions
                    if len(questions) < num_questions:
                        st.warning(f"Only {len(questions)}/{num_questions} questions generated. Check debug output for details: {parse_error}")
                        if "Incomplete row" in parse_error and attempt < max_attempts - 1:
                            st.info(f"Retrying due to incomplete output (Attempt {attempt + 2}/{max_attempts})...")
                            continue
                    else:
                        st.success(f"Generated {len(questions)} question(s).")
                    break
                else:
                    st.error(f"❌ Could not parse questions (Attempt {attempt + 1}/{max_attempts}). Reason: {parse_error}")
                    st.session_state.raw_output = output_text[:1000]
                    if "Incomplete row" in parse_error and attempt < max_attempts - 1:
                        st.info(f"Retrying due to incomplete output (Attempt {attempt + 2}/{max_attempts})...")
                        continue
            except Exception as e:
                st.error(f"❌ Generation error (Attempt {attempt + 1}/{max_attempts}): {str(e)}")
                st.session_state.raw_output = str(e)
        else:
            st.error("❌ Failed to generate valid questions after retries.")

# --- Render Current Question ---
if st.session_state.get("questions"):
    q_index = st.session_state.get("q_index", 0)
    if 0 <= q_index < len(st.session_state.questions):
        question = st.session_state.questions[q_index]
        st.markdown(f"### Question {q_index + 1}")
        st.write(question["q"])

        # Extract choices
        letters_order = [m.group(1) for c in question["choices"] if (m := re.match(r"([A-Z])\.\s*(.*)", c))]
        label_map = {m.group(1): m.group(2) for c in question["choices"] if (m := re.match(r"([A-Z])\.\s*(.*)", c))}

        with st.form(f"question_{q_index}"):
            if question["type"] == "mcq":
                selected_display = st.radio("Choose one:", question["choices"], index=None, key=f"mcq_{q_index}")
                selected_letters = [selected_display.split(".", 1)[0]] if selected_display else []
            else:
                selected_letters = []
                for L in letters_order:
                    if st.checkbox(f"{L}. {label_map[L]}", key=f"sata_{q_index}_{L}"):
                        selected_letters.append(L)

            submit_clicked = st.form_submit_button("Submit")

            if submit_clicked:
                if not selected_letters:
                    st.warning("Please select at least one option.")
                else:
                    st.session_state.answered = True
                    st.session_state.selected_letters = selected_letters

        # Display results
        if st.session_state.get("answered"):
            if not st.session_state.scored_questions.get(q_index, False):
                selected_set = set(st.session_state.selected_letters)
                correct_set = set(question["correct_set"])
                if selected_set == correct_set:
                    st.success(f"✅ Correct! Answer(s): {', '.join(sorted(correct_set))}")
                    st.session_state.score += 1
                else:
                    st.error(f"❌ Incorrect. Correct answer(s): {', '.join(sorted(correct_set))}")
                st.session_state.scored_questions[q_index] = True

            # YouTube link
            search_term = question.get("search_term", "").strip()
            if search_term:
                yt_url = f"https://www.youtube.com/results?search_query={urlparse.quote(search_term)}"
                st.markdown("#### 🔎 Find related videos")
                st.caption(search_term[:60])
                st.link_button("Search on YouTube", yt_url)

            # Rationales
            st.markdown("#### 💡 Rationales")
            for L in letters_order:
                expl = question["rationales"].get(L, "")
                is_correct = L in question["correct_set"]
                is_selected = L in st.session_state.selected_letters
                box = st.success if is_correct and is_selected else st.info if is_correct else st.warning if is_selected else st.info
                prefix = "✅ Correct" if is_correct and is_selected else "ℹ️ Correct (not selected)" if is_correct else "⚠️ Incorrect" if is_selected else "ℹ️ Not correct"
                box(f"{L}. {label_map[L]}\n\n💡 {expl or 'No rationale provided.'}")

            st.write(f"📊 Score: {st.session_state.score}/{q_index + 1}")

            # Navigation
            if q_index < len(st.session_state.questions) - 1:
                if st.button("Next Question"):
                    st.session_state.q_index += 1
                    st.session_state.answered = False
                    st.session_state.selected_letters = []
                    st.rerun()
            else:
                st.success(f"🎉 Quiz complete! Final Score: {st.session_state.score}/{len(st.session_state.questions)}")

        # Handle missing questions
        total_q = len(st.session_state.questions)
        expected_q = st.session_state.get("expected_count", total_q)
        if total_q < expected_q:
            missing_q = expected_q - total_q
            st.warning(f"Only {total_q}/{expected_q} questions generated.")
            if st.button(f"Generate {missing_q} more"):
                try:
                    output_text = generate_questions(topic, difficulty, missing_q, question_type_percent)
                    new_qs, parse_error = parse_questions(output_text)
                    if new_qs:
                        st.session_state.questions.extend(new_qs)
                        st.success(f"Added {len(new_qs)} question(s).")
                        st.rerun()
                    else:
                        st.error(f"Could not parse additional questions: {parse_error}")
                except Exception as e:
                    st.error(f"Error: {e}")

# --- Debug Tools ---
if st.session_state.get("raw_output"):
    with st.expander("Debug: Raw Output"):
        st.write(f"Format: {st.session_state.get('raw_format', 'unknown')}")
        st.code(st.session_state.raw_output, language=st.session_state.get("raw_format", "text"))
        st.download_button("Download raw output", st.session_state.raw_output, file_name="raw_output.txt")

with st.expander("Debug: Test CSV Parser"):
    sample_csv = st.text_area("Paste CSV here:", height=200)
    if st.button("Parse CSV"):
        if sample_csv.strip():
            parsed, parse_error = parse_questions_from_csv(sample_csv)
            st.write(f"Parsed {len(parsed)} question(s). Error: {parse_error}")
            if parsed:
                st.json(parsed[0])
        else:
            st.warning("Paste some CSV text.")