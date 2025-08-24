import streamlit as st
import google.generativeai as genai
import re
import os

# --- Setup Gemini ---
API_KEY = st.secrets.get("API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("API_KEY")
if not API_KEY or API_KEY == "YOUR_KEY_HERE":
    st.error("Missing API key. Add API_KEY to .streamlit/secrets.toml or set GOOGLE_API_KEY/API_KEY in env.")
    st.stop()

genai.configure(api_key=API_KEY)
MODEL_NAME = st.secrets.get("GEMINI_MODEL", "gemini-2.5-flash")
model = genai.GenerativeModel(MODEL_NAME)


# --- Parse Gemini output for 2.5 Flash single-line choices ---
def parse_questions(output_text):
    parsed = []
    blocks = re.split(r"(?i)(?=Question \d+:)", output_text)
    for block in blocks:
        block = block.strip()
        if not block.lower().startswith("question"):
            continue

        # Question text: from "Question X:" up to first A.
        q_match = re.match(r"Question \d+:\s*(.*?)\s*A\.", block, re.DOTALL)
        question = q_match.group(1).strip() if q_match else None

        # Choices: find all A.–D. with quoted text
        raw_choices = re.findall(r'([A-D])\. "(.*?)"', block)
        choices = [f"{letter}. {text}" for letter, text in raw_choices]

        # Correct answer
        a_match = re.search(r"Answer:\s*([A-D])", block, re.IGNORECASE)
        correct = a_match.group(1).upper() if a_match else "?"

        # Rationale: only for correct answer
        r_match = None
        for i, (letter, text) in enumerate(raw_choices):
            if letter == correct:
                start = block.find(f'{letter}. "{text}"') + len(f'{letter}. "{text}"')
                end = len(block)
                if i + 1 < len(raw_choices):
                    next_letter = raw_choices[i + 1][0]
                    end = block.find(f'{next_letter}. "')
                r_match = block[start:end].strip()
                break

        rationale = r_match if r_match else "No rationale provided."

        if question and choices:
            parsed.append({
                "q": question,
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
            prompt = f"Create {num_questions} NCLEX-style multiple choice questions on {topic} with answers and rationales."
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
            st.session_state.selected = choice[0]  # first letter A–D
            st.session_state.answered = True

    # After answer selected
    if st.session_state.answered:
        # Only score once per question
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
