import os
import re
import json
import csv
import io
import time
import urllib.parse as urlparse
import streamlit as st
import google.generativeai as genai
import typing_extensions as typing

# --- API key and model setup ---
API_KEY = st.secrets.get("API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("API_KEY")
if not API_KEY:
    st.error("Missing API key. Add API_KEY to .streamlit/secrets.toml or set GOOGLE_API_KEY/API_KEY in env.")
    st.stop()
genai.configure(api_key=API_KEY)
MODEL_NAME = st.secrets.get("GEMINI_MODEL", "gemini-2.5-flash") 
model = genai.GenerativeModel(MODEL_NAME)

# --- Define Expected Output Schema ---
class QuestionResponse(typing.TypedDict):
    question_text: str
    question_type: str
    options: dict[str, str]
    correct_answers: list[str]
    rationales: dict[str, str]
    youtube_search_term: str

class QuizOutput(typing.TypedDict):
    questions: list[QuestionResponse]

# --- Optimized Parsing Functions ---
def parse_questions_from_json(output_text: str) -> tuple[list, str]:
    """Parse JSON output into structured questions."""
    if not output_text.strip():
        return [], "Empty input text."

    try:
        data = json.loads(output_text)
        if not isinstance(data, dict) or "questions" not in data:
            return [], "JSON does not contain 'questions' list."
    except json.JSONDecodeError as e:
        return [], f"Invalid JSON format: {str(e)}"

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
            skipped_reasons.append(f"Question {q_idx}: Required {min_options} options, found {len(letters)}")
            continue

        correct_set = set()
        for ans in q.get("correct_answers", []):
            L = ans.strip().upper()
            if L in letters:
                correct_set.add(L)

        if not correct_set:
            skipped_reasons.append(f"Question {q_idx}: No valid correct_answers")
            continue

        rationales_map = {L: q.get("rationales", {}).get(L, "") for L in letters}

        if question_text and choices and correct_set:
            parsed.append({
                "q": question_text,
                "type": "sata" if is_sata else "mcq",
                "choices": choices,
                "correct_set": sorted(list(correct_set)),
                "rationales": rationales_map,
                "search_term": q.get("youtube_search_term", "").strip(),
            })
        else:
            skipped_reasons.append(f"Question {q_idx}: Missing question_text, choices, or correct answers")

    error_msg = "Parsed successfully" if parsed else "No valid questions parsed from JSON."
    if skipped_reasons:
        error_msg += f"\nSkipped questions: {'; '.join(skipped_reasons)}"
    if parsed:
        st.session_state["raw_format"] = "json"
    
    return parsed, error_msg

# --- Initialize Session State ---
def init_session_state():
    """Initialize all session state variables with defaults."""
    defaults = {
        "questions": [],
        "q_index": 0,
        "answered": False,
        "selected_letters": [],
        "score": 0,
        "scored_questions": {},
        "raw_output": "",
        "raw_format": "unknown",
        "expected_count": 0,
        "topic": "",
        "difficulty": "",
        "question_type_percent": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session_state()

# --- Streamlit UI with Form ---
st.title("🏥 NCLEX Smart Q&A Tutor 🏥")
st.subheader("Developed by Glenn Heydolph ADN '26 DSC, BSN '27 UCF")

with st.form("quiz_settings"):
    difficulty = st.selectbox("Select difficulty level:", ["Easy", "Medium", "Hard"])
    question_type_percent = st.selectbox("Select percentage of SATA Questions:", ["0", "25", "50", "75", "100"], index=2)
    topic = st.text_input("Enter a topic:", "Heart Failure", help="Enter a nursing-related topic (e.g., Heart Failure, Diabetes, Wound Care)")
    num_questions = st.number_input("Number of questions:", min_value=1, max_value=20, value=2, step=1)
    submitted = st.form_submit_button("Generate Questions")

# --- Generate Questions ---

@st.cache_data
def generate_questions(topic: str, difficulty: str, num_questions: int, question_type_percent: str, batch_id: int) -> str:
    """Cache the model response to avoid redundant API calls."""
    prompt = f"""# ROLE
You are an NCLEX Test Development Specialist. Generate exactly {num_questions} high-quality, clinically accurate NCLEX-style questions on "{topic}" at {difficulty} difficulty.

# QUESTION TYPE DISTRIBUTION
- {question_type_percent}% SATA (Select All That Apply), remainder MCQ (Multiple Choice)
- MCQ: Exactly 4 choices (A-D) with 1 correct answer.
- SATA: Exactly 6 choices (A-F) with 2-4 correct answers.

# CONTENT & DISTRACTOR REQUIREMENTS
- Clinical Realism: Use realistic scenarios, current evidence-based practices (2020+)
- Cognitive Level: Focus on Application, Analysis, and Evaluation (avoid simple recall)
- NCLEX Integration: Incorporate nursing process, safety, ABC priority, therapeutic communication
- Client-Centered: Use "client" not "patient"; avoid judgmental language
- Distractors: Must be plausible but clearly incorrect. No "all of the above" or "none of the above". Include common misconceptions or errors.
- Rationales: 10-25 words max per rationale, explaining WHY it is correct/incorrect. Teach the underlying principle.
- YouTube Keyword: Provide 2-4 words that yield quality nursing education videos (e.g., "heart failure nursing").
- Data Formatting:
    - `question_type` must be "mcq" or "sata"
    - `options` should map the choice letter to its text (e.g., {{"A": "Option text"}})
    - `correct_answers` should be a list of correct letters (e.g., ["A", "C"])
    - `rationales` should map the option letter to its explanation
"""
    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                response_schema=QuizOutput,
                temperature=0.4,
            ),
        )
        return response.text if hasattr(response, "text") else str(response)
    except Exception as e:
        error_msg = str(e)
        if "quota" in error_msg.lower():
            return "Error: API quota exceeded. Please check your Google AI Studio quota and try again later."
        elif "invalid" in error_msg.lower() and "key" in error_msg.lower():
            return "Error: Invalid API key. Please check your API_KEY in secrets.toml."
        elif "blocked" in error_msg.lower():
            return f"Error: Content was blocked by safety filters. Try a different topic. Details: {error_msg}"
        else:
            return f"Error: {error_msg}"

# --- Batching logic for safe question generation ---
def batched_generate_questions(topic: str, difficulty: str, num_questions: int, question_type_percent: str, batch_size: int = 2) -> str:
    """Generate questions in batches, parsing and combining structured JSON objects."""
    all_questions = []
    questions_remaining = num_questions
    batch_idx = 0
    while questions_remaining > 0:
        n = min(batch_size, questions_remaining)
        out = generate_questions(topic, difficulty, n, question_type_percent, batch_id=batch_idx)
        
        # If the request fails, the string starts with 'Error:'
        if out.startswith("Error:"):
            raise Exception(out)

        try:
            batch_data = json.loads(out)
            if "questions" in batch_data:
                all_questions.extend(batch_data["questions"])
        except json.JSONDecodeError:
            pass # We'll handle overall failure downstream

        questions_remaining -= n
        batch_idx += 1
        
    # Return a combined JSON string
    return json.dumps({"questions": all_questions})

if submitted:
    # Validate topic input
    if not topic or not topic.strip():
        st.error("❌ Please enter a topic before generating questions.")
        st.stop()
    
    # Store settings for "Generate more" functionality
    st.session_state.topic = topic
    st.session_state.difficulty = difficulty
    st.session_state.question_type_percent = question_type_percent
    
    with st.spinner("Generating questions..."):
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                # Use batching to avoid truncation
                output_text = batched_generate_questions(topic, difficulty, num_questions, question_type_percent, batch_size=2)
                questions, parse_error = parse_questions_from_json(output_text)
                if questions:
                    st.session_state.questions = questions
                    st.session_state.q_index = 0
                    st.session_state.answered = False
                    st.session_state.selected_letters = []
                    st.session_state.score = 0
                    st.session_state.scored_questions = {}
                    st.session_state.raw_output = output_text[:2000]  # Store more for debugging
                    st.session_state.expected_count = num_questions
                    if len(questions) < num_questions:
                        st.warning(f"⚠️ Only {len(questions)}/{num_questions} questions generated. Check debug output for details.")
                        if attempt < max_attempts - 1:
                            st.info(f"Retrying incomplete generation (Attempt {attempt + 2}/{max_attempts})...")
                            time.sleep(2)
                            continue
                    else:
                        st.success(f"✅ Successfully generated {len(questions)} question(s)!")
                    break
                else:
                    st.error(f"❌ Could not parse questions (Attempt {attempt + 1}/{max_attempts}). Reason: {parse_error}")
                    st.session_state.raw_output = output_text[:2000]
                    if attempt < max_attempts - 1:
                        st.info(f"Retrying (Attempt {attempt + 2}/{max_attempts})...")
                        time.sleep(2)
                        continue
            except Exception as e:
                error_msg = str(e)
                st.error(f"❌ Generation error (Attempt {attempt + 1}/{max_attempts}): {error_msg}")
                st.session_state.raw_output = error_msg
                if "quota" in error_msg.lower() or "rate" in error_msg.lower():
                    st.error("⚠️ API quota or rate limit exceeded. Please try again later.")
                    break
        else:
            st.error("❌ Failed to generate valid questions after multiple retries. Please check your API key and try a different topic.")

# --- Render Current Question ---
if st.session_state.get("questions"):
    q_index = st.session_state.get("q_index", 0)
    if 0 <= q_index < len(st.session_state.questions):
        question = st.session_state.questions[q_index]
        
        # Progress bar
        total_questions = len(st.session_state.questions)
        progress = (q_index + 1) / total_questions
        st.progress(progress, text=f"Question {q_index + 1} of {total_questions}")
        
        st.markdown(f"### Question {q_index + 1}")
        question_type_display = "📋 Select All That Apply (SATA)" if question["type"] == "sata" else "🔘 Multiple Choice"
        st.caption(question_type_display)
        st.write(question["q"])

        # Extract choices - Fix: Pre-compute matches to avoid walrus operator issues
        choice_matches = [(re.match(r"([A-Z])\.\s*(.*)", c), c) for c in question["choices"]]
        letters_order = [m.group(1) for m, c in choice_matches if m]
        label_map = {m.group(1): m.group(2) for m, c in choice_matches if m}

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
                yt_url = f"https://www.youtube.com/results?search_query=Nursing {urlparse.quote(search_term)}"
                st.markdown("#### 🔎 Find related videos")
                st.caption(search_term[:60])
                st.link_button("Search on YouTube", yt_url)

            # Rationales
            st.markdown("#### 💡 Rationales")
            for L in letters_order:
                expl = question["rationales"].get(L, "")
                is_correct = L in question["correct_set"]
                is_selected = L in st.session_state.selected_letters
                
                # Determine styling based on correctness
                if is_correct and is_selected:
                    st.success(f"**{L}. {label_map[L]}**\n\n💡 {expl or 'No rationale provided.'}")
                elif is_correct:
                    st.info(f"**{L}. {label_map[L]}** _(Correct, not selected)_\n\n💡 {expl or 'No rationale provided.'}")
                elif is_selected:
                    st.error(f"**{L}. {label_map[L]}** _(Incorrect)_\n\n💡 {expl or 'No rationale provided.'}")
                else:
                    with st.expander(f"{L}. {label_map[L]}"):
                        st.write(f"💡 {expl or 'No rationale provided.'}")

            st.write(f"📊 Score: {st.session_state.score}/{q_index + 1}")

            # Navigation
            col1, col2 = st.columns([1, 1])
            with col1:
                if q_index < len(st.session_state.questions) - 1:
                    if st.button("➡️ Next Question", use_container_width=True):
                        st.session_state.q_index += 1
                        st.session_state.answered = False
                        st.session_state.selected_letters = []
                        st.rerun()
                else:
                    percentage = (st.session_state.score / len(st.session_state.questions)) * 100
                    st.success(f"🎉 Quiz complete! Final Score: {st.session_state.score}/{len(st.session_state.questions)} ({percentage:.1f}%)")
            
            with col2:
                if st.button("🔄 Start New Quiz", use_container_width=True):
                    # Clear quiz state but keep settings
                    st.session_state.questions = []
                    st.session_state.q_index = 0
                    st.session_state.answered = False
                    st.session_state.selected_letters = []
                    st.session_state.score = 0
                    st.session_state.scored_questions = {}
                    st.session_state.expected_count = 0
                    st.rerun()

        # Handle missing questions - Fix: Use stored settings from session state
        total_q = len(st.session_state.questions)
        expected_q = st.session_state.get("expected_count", total_q)
        if total_q < expected_q:
            missing_q = expected_q - total_q
            st.warning(f"⚠️ Only {total_q}/{expected_q} questions generated.")
            if st.button(f"Generate {missing_q} more question(s)"):
                # Retrieve stored settings
                stored_topic = st.session_state.get("topic", "")
                stored_difficulty = st.session_state.get("difficulty", "Medium")
                stored_percent = st.session_state.get("question_type_percent", "50")
                
                if not stored_topic:
                    st.error("❌ Cannot generate more questions. Please start a new quiz.")
                else:
                    try:
                        with st.spinner(f"Generating {missing_q} additional question(s)..."):
                            output_text = batched_generate_questions(stored_topic, stored_difficulty, missing_q, stored_percent, batch_size=2)
                            new_qs, parse_error = parse_questions_from_json(output_text)
                            if new_qs:
                                st.session_state.questions.extend(new_qs)
                                st.session_state.expected_count = len(st.session_state.questions)
                                st.success(f"✅ Added {len(new_qs)} question(s).")
                                st.rerun()
                            else:
                                st.error(f"❌ Could not parse additional questions: {parse_error}")
                    except Exception as e:
                        st.error(f"❌ Error generating additional questions: {str(e)}")

# --- Debug Tools ---
st.divider()
st.caption("🔧 Developer Tools")

if st.session_state.get("raw_output"):
    with st.expander("📋 Debug: Raw API Output"):
        st.write(f"**Format:** {st.session_state.get('raw_format', 'unknown')}")
        st.code(st.session_state.raw_output, language=st.session_state.get("raw_format", "text"))
        st.download_button(
            "⬇️ Download Raw Output", 
            st.session_state.raw_output, 
            file_name="raw_output.txt",
            help="Download the raw API response for debugging"
        )

with st.expander("🧪 Debug: Test JSON Parser"):
    st.info("Use this tool to test the JSON parser with your own data.")
    sample_json = st.text_area("Paste JSON here:", height=200, placeholder="Paste JSON formatted question data here...")
    if st.button("Parse JSON", type="primary"):
        if sample_json.strip():
            parsed, parse_error = parse_questions_from_json(sample_json)
            st.write(f"**Result:** Parsed {len(parsed)} question(s).")
            if parse_error:
                st.warning(f"**Parse Messages:** {parse_error}")
            if parsed:
                st.json(parsed[0])
        else:
            st.warning("⚠️ Please paste some JSON text first.")