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
        question_text = q.get("question_text", "").strip()
        options = q.get("options", {}) or {}
        correct = (q.get("correct_answer") or "?").strip().upper()
        rationales = q.get("rationales", {}) or {}

        # Build choices in A-D order if present
        choices = [f"{letter}. {options[letter]}" for letter in ["A", "B", "C", "D"] if letter in options]

        # Pick rationale for the correct option (fallback to empty string)
        rationale = rationales.get(correct, "").strip()

        if question_text and choices:
            parsed.append({
                "q": question_text,
                "choices": choices,
                "correct": correct,
                "rationale": rationale
            })

    return parsed


# --- Streamlit UI ---
st.title("💊 NCLEX Smart Tutor")

topic = st.text_input("Enter a topic:", "Heart Failure")
num_questions = st.number_input("Number of questions:", min_value=1, max_value=20, value=5, step=1)

# --- Generate Questions ---
if st.button("Generate Questions"):
    with st.spinner("Calling Gemini..."):
        try:
            prompt = (
                f"Create {num_questions} NCLEX-style multiple choice questions on {topic} with answers and rationales. "
                "Format the output as valid JSON using this template:\n\n"
                "{\n"
                '  "questions": [\n'
                "    {\n"
                '      "question_number": "QUESTION_NUMBER",\n'
                '      "question_text": "QUESTION_TEXT",\n'
                '      "options": {\n'
                '        "A": "OPTION_A",\n'
                '        "B": "OPTION_B",\n'
                '        "C": "OPTION_C",\n'
                '        "D": "OPTION_D"\n'
                "      },\n"
                '      "correct_answer": "CORRECT_ANSWER",\n'
                '      "rationales": {\n'
                '        "A": "RATIONALE_A",\n'
                '        "B": "RATIONALE_B",\n'
                '        "C": "RATIONALE_C",\n'
                '        "D": "RATIONALE_D"\n'
                "      }\n"
                "    }\n"
                "  ]\n"
                "}\n"
                "Do not include any text or explanation before or after the JSON."
            )
            response = model.generate_content(prompt)
            output_text = response.text if hasattr(response, "text") else str(response)

            questions = parse_questions(output_text)
            st.session_state.questions = questions
            st.session_state.q_index = 0
            st.session_state.answered = False
            st.session_state.selected = None
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

    # Answer buttons
    for i, choice in enumerate(question["choices"]):
        if st.button(choice, key=f"choice_{q_index}_{i}"):
            st.session_state.selected = choice[0]  # first letter A-D
            st.session_state.answered = True

    # After answer selected
    if st.session_state.answered:
        if not st.session_state.scored_questions.get(q_index, False):
            if st.session_state.selected == question["correct"]:
                st.success(f"✅ Correct! The answer is {question['correct']}.")
                st.session_state.score += 1
            else:
                st.error(f"❌ Incorrect. The correct answer is {question['correct']}.")
            st.session_state.scored_questions[q_index] = True

        st.info(f"💡 Rationale: {question['rationale']}")
        st.write(f"📊 Score: {st.session_state.score}/{q_index+1}")

        # Next question or finish
        if q_index < len(st.session_state.questions) - 1:
            if st.button("➡️ Next Question"):
                st.session_state.q_index += 1
                st.session_state.answered = False
                st.session_state.selected = None
                st.rerun()
        else:
            st.success(f"🎉 Quiz complete! Final Score: {st.session_state.score}/{len(st.session_state.questions)}")
            st.session_state.completed = True
