import streamlit as st
import google.generativeai as genai
import re
import os

# --- Setup Gemini ---
API_KEY = os.environ.get("API_KEY") or "YOUR_KEY_HERE"
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")


# --- Parse Gemini output ---
def parse_questions(output_text):
    parsed = []
    blocks = re.split(r"(?i)(?=Question[:])", output_text)
    for block in blocks:
        block = block.strip()
        if not block or not block.lower().startswith("question"):
            continue

        q_match = re.search(r"Question:\s*(.*?)(?=Choices:)", block, re.IGNORECASE | re.DOTALL)
        question = q_match.group(1).strip() if q_match else None

        c_match = re.search(r"Choices:\s*(.*?)(?=Answer:)", block, re.IGNORECASE | re.DOTALL)
        choices_text = c_match.group(1).strip() if c_match else ""
        raw_choices = re.findall(r"[A-D][\).]?\s*(.+)", choices_text)
        choices = [f"{chr(65+i)}. {c.strip()}" for i, c in enumerate(raw_choices)]

        a_match = re.search(r"Answer:\s*([A-D])", block, re.IGNORECASE)
        correct = a_match.group(1).upper() if a_match else "?"

        r_match = re.search(r"Rationale:\s*(.*?)(?=(?:Question:|$))", block, re.IGNORECASE | re.DOTALL)
        rationale = r_match.group(1).strip() if r_match else "No rationale provided."

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

if st.button("Generate Questions"):
    with st.spinner("Calling Gemini..."):
        try:
            prompt = f"Create 5 NCLEX-style multiple choice questions on {topic} with answers and rationales."
            response = model.generate_content(prompt)
            output_text = response.text if hasattr(response, "text") else str(response)

            questions = parse_questions(output_text)
            st.session_state.questions = questions
            st.session_state.q_index = 0
            st.session_state.answered = False
            st.session_state.selected = None
            st.session_state.raw_output = output_text
            st.session_state.score = 0  # reset score
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
    for choice in question["choices"]:
        if st.button(choice, key=f"choice_{q_index}_{choice}"):
            st.session_state.selected = choice[0]
            st.session_state.answered = True

    # After answer selected
    if st.session_state.answered:
        if st.session_state.selected == question["correct"]:
            st.success(f"✅ Correct! The answer is {question['correct']}.")
            if "score" in st.session_state:
                st.session_state.score += 1
        else:
            st.error(f"❌ Incorrect. The correct answer is {question['correct']}.")

        st.info(f"💡 Rationale: {question['rationale']}")

        st.write(f"📊 Score: {st.session_state.score}/{q_index+1}")

        if q_index < len(st.session_state.questions) - 1:
            if st.button("➡️ Next Question"):
                st.session_state.q_index += 1
                st.session_state.answered = False
                st.session_state.selected = None
                st.rerun()
        else:
            st.success(f"🎉 Quiz complete! Final Score: {st.session_state.score}/{len(st.session_state.questions)}")
            st.session_state.completed = True
