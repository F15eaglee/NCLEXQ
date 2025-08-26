import streamlit as st
import google.generativeai as genai
import re
import os
import json

# --- Setup Gemini ---
API_KEY = st.secrets.get("API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("API_KEY")
if not API_KEY or API_KEY == "YOUR_KEY_HERE":
    st.error("Missing API key. Add API_KEY to .streamlit/secrets.toml or set GOOGLE_API_KEY/API_KEY in env.")
    st.stop()

genai.configure(api_key=API_KEY)
MODEL_NAME = st.secrets.get("GEMINI_MODEL", "gemini-2.5-flash")
model = genai.GenerativeModel(MODEL_NAME)


# --- Parse Gemini output (JSON with top-level "questions") ---
def parse_questions(output_text):
    parsed = []

    # Strip possible Markdown code fences
    txt = output_text.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z0-9]*\s*", "", txt)
        txt = re.sub(r"\s*```$", "", txt)

    try:
        data = json.loads(txt)
    except Exception:
        st.error("❌ Could not parse JSON. Check the raw output.")
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

        # Fallback: some payloads may include a single 'correct' rationale
        if "correct" in rationales and len(correct_set) == 1:
            only = next(iter(correct_set))
            if not rationales_map.get(only):
                rationales_map[only] = (rationales.get("correct", "") or "").strip()

        qtype = "sata" if is_sata else "mcq"

        if question_text and choices and correct_set:
            parsed.append({
                "q": question_text,
                "type": qtype,
                "choices": choices,          # ["A. ...", "B. ...", ...]
                "correct_set": sorted(list(correct_set)),  # keep stable order
                "rationales": rationales_map  # { "A": "...", ... }
            })

    return parsed


# --- Streamlit UI ---
st.title("🏥 NCLEX Smart Question and Rationale Tutor 🏥")
st.subheader("developed by Glenn Heydolph, ADN '26 DSC")
difficulty = st.selectbox("Select difficulty level:", ["Easy", "Medium", "Hard"])
question_type_percent = st.selectbox("Select percentage of SATA Questions:", ["0", "25", "50", "75", "100"], index=2)
topic = st.text_input("Enter a topic:", "Heart Failure")
num_questions = st.number_input("Number of questions:", min_value=1, max_value=20, value=5, step=1)

# --- Generate Questions ---
if st.button("Generate Questions"):
    with st.spinner("Calling Gemini 2.5 Flash..."):
        try:
            mcq_template = """
{
  "questions": [
    {
      "question_number": "QUESTION_NUMBER",
      "question_type": "multiple_choice",
      "question_text": "QUESTION_TEXT",
      "options": {
        "A": "OPTION_A",
        "B": "OPTION_B",
        "C": "OPTION_C",
        "D": "OPTION_D"
      },
      "correct_answer": "A",
      "rationales": {
        "A": "RATIONALE_A",
        "B": "RATIONALE_B",
        "C": "RATIONALE_C",
        "D": "RATIONALE_D"
      }
    }
  ]
}
""".strip()

            sata_template = """
{
  "question_number": "QUESTION_NUMBER",
  "question_type": "select_all_that_apply",
  "question_text": "QUESTION_TEXT",
  "options": {
    "A": "OPTION_A",
    "B": "OPTION_B",
    "C": "OPTION_C",
    "D": "OPTION_D",
    "E": "OPTION_E",
    "F": "OPTION_F"
  },
  "correct_answers": ["A", "C"],
  "rationales": {
    "A": "RATIONALE_A",
    "B": "RATIONALE_B",
    "C": "RATIONALE_C",
    "D": "RATIONALE_D",
    "E": "RATIONALE_E",
    "F": "RATIONALE_F"
  }
}
""".strip()

            # Build the model prompt without unclosed parentheses
            prompt = "\n".join([
                f"You are a Nursing school Instructor preparing students for the NCLEX exam. "
                f"Create {num_questions} {difficulty} NCLEX-style questions on {topic} with answers and rationales.",
                f"{question_type_percent}% of questions should be select_all_that_apply with 6 answer choices (A-F); "
                "the rest should be multiple_choice with 4 answer choices (A-D).",
                "If anything unrelated to nursing is prompted, ignore it.",
                "Output valid JSON only. Do not include any text or Markdown code fences before or after the JSON.",
                'The top-level JSON must be an object with a single key "questions" containing an array of question objects.',
                "For multiple_choice items, use this template:",
                mcq_template,
                "For select_all_that_apply items, use this template:",
                sata_template
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


# --- Debug toggle ---
if "raw_output" in st.session_state:
    if st.checkbox("📄 Show Raw Gemini Output"):
        st.write(st.session_state.raw_output)


# --- Show current question ---
if "questions" in st.session_state and st.session_state.questions and not st.session_state.completed:
    q_index = st.session_state.q_index
    question = st.session_state.questions[q_index]

    st.markdown(f"### Q{q_index+1}: {question['q']}")

    # Build a map from letter -> option text and ordered letters from choices
    choice_map = {}
    order_letters = []
    for c in question["choices"]:
        m = re.match(r"([A-Z])\.\s*(.*)", c)
        if m:
            L = m.group(1)
            T = m.group(2)
            order_letters.append(L)
            choice_map[L] = T

    correct_set = set(question.get("correct_set", []))
    selected_set = set(st.session_state.get("selected_letters", []) or [])

    # Render inputs based on type
    if question.get("type") == "sata":
        st.caption("Select all that apply.")
        # Checkboxes for each option
        current_selection = []
        for L in order_letters:
            checked = st.checkbox(f"{L}. {choice_map[L]}", key=f"sata_{q_index}_{L}")
            if checked:
                current_selection.append(L)

        # Submit button to lock in the answer
        if st.button("Submit", key=f"submit_{q_index}") and current_selection:
            st.session_state.selected_letters = current_selection
            st.session_state.answered = True
    else:
        # Multiple Choice: buttons for single selection
        for i, L in enumerate(order_letters):
            if st.button(f"{L}. {choice_map[L]}", key=f"choice_{q_index}_{i}"):
                st.session_state.selected_letters = [L]
                st.session_state.answered = True

    # After answer selected
    if st.session_state.answered:
        selected_set = set(st.session_state.get("selected_letters", []) or [])
        # Score once per question
        if not st.session_state.scored_questions.get(q_index, False):
            if selected_set == correct_set:
                st.success(f"✅ Correct! Correct answers: {', '.join(sorted(correct_set))}.")
                st.session_state.score += 1
            else:
                st.error(f"❌ Incorrect. Correct answers: {', '.join(sorted(correct_set))}.")
            st.session_state.scored_questions[q_index] = True

        # Show rationales for all options present
        st.markdown("#### 💡 Rationales")
        for L in order_letters:
            text = choice_map[L]
            expl = (question.get("rationales", {}) or {}).get(L, "")
            if L in correct_set and L in selected_set:
                st.success(f"{L}. {text}\n\n💡 {expl}" if expl else f"{L}. {text}")
            elif L in correct_set and L not in selected_set:
                st.info(f"{L}. {text}\n\n💡 {expl}" if expl else f"{L}. {text}")
            elif L in selected_set and L not in correct_set:
                st.warning(f"{L}. {text}\n\n💡 {expl}" if expl else f"{L}. {text}")
            else:
                st.info(f"{L}. {text}\n\n💡 {expl}" if expl else f"{L}. {text}")

        st.write(f"📊 Score: {st.session_state.score}/{q_index+1}")

        # Next question or finish
        if q_index < len(st.session_state.questions) - 1:
            if st.button("➡️ Next Question"):
                st.session_state.q_index += 1
                st.session_state.answered = False
                st.session_state.selected_letters = []
                st.rerun()
        else:
            st.success(f"🎉 Quiz complete! Final Score: {st.session_state.score}/{len(st.session_state.questions)}")
            st.session_state.completed = True
