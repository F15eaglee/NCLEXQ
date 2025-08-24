import streamlit as st
import google.generativeai as genai
import os
import re

# --- Setup Gemini ---
API_KEY = os.environ.get("API_KEY") or "YOUR_KEY_HERE"
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

st.title("💊 NCLEX Smart Tutor")

# --- User input ---
topic = st.text_input("Enter a topic:", "Heart Failure")
num_questions = st.slider("Number of questions", 1, 5, 2)

# --- Initialize session state ---
if "questions" not in st.session_state:
    st.session_state.questions = []


def parse_questions(output_text):
    """Parse Gemini output into structured questions"""
    blocks = re.split(r"(?=Question:)", output_text, flags=re.IGNORECASE)
    parsed = []

    for i, block in enumerate(blocks[1:], 1):
        q_match = re.search(r"Question:\s*(.+)", block, re.IGNORECASE)
        c_match = re.search(r"Choices:\s*(.+?)(?=Answer:)", block, re.IGNORECASE | re.DOTALL)
        a_match = re.search(r"Answer:\s*(.+)", block, re.IGNORECASE)
        r_match = re.search(r"Rationale:\s*(.+)", block, re.IGNORECASE | re.DOTALL)

        if not q_match or not c_match:
            continue

        question = q_match.group(1).strip()
        choices_text = c_match.group(1).strip()
        choices = re.split(r"[A-D][\).]", choices_text)
        choices = [c.strip() for c in choices if c.strip()]

        correct = a_match.group(1).strip() if a_match else "Unknown"
        rationale = r_match.group(1).strip() if r_match else "No rationale provided."

        parsed.append({
            "q": question,
            "choices": choices,
            "correct": correct,
            "rationale": rationale
        })
    return parsed


# --- Generate new questions ---
if st.button("Generate Questions"):
    with st.spinner("Calling Gemini..."):
        try:
            prompt = f"""
            You are an NCLEX exam question generator.
            Create {num_questions} NCLEX-style multiple choice questions on the topic: {topic}.
            Each question should include:
            - The question text
            - Four answer choices (A, B, C, D)
            - The correct answer clearly marked
            - A rationale
            Format exactly:

            Question:
            Choices:
            Answer:
            Rationale:
            """

            response = model.generate_content(prompt)
            output_text = response.text if hasattr(response, "text") else str(response)

            st.session_state.questions = parse_questions(output_text)

            with st.expander("📄 Raw Gemini Output"):
                st.write(output_text)

        except Exception as e:
            st.error(f"Error: {e}")


# --- Display questions from session_state ---
for i, qdata in enumerate(st.session_state.questions, 1):
    st.markdown(f"### Q{i}: {qdata['q']}")

    choice = st.radio(
        f"Select your answer for Q{i}:",
        qdata["choices"],
        key=f"choice_{i}"
    )

    if st.button(f"Check Answer Q{i}", key=f"check_{i}"):
        if choice.lower().startswith(qdata["correct"].lower()[0].lower()):
            st.success(f"✅ Correct! The answer is {qdata['correct']}.")
        else:
            st.error(f"❌ Incorrect. The correct answer is {qdata['correct']}.")
        st.info(f"💡 Rationale: {qdata['rationale']}")
