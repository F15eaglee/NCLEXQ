import streamlit as st
import google.generativeai as genai
import re
import os

# --- Setup Gemini ---
API_KEY = os.environ["API_KEY"]  # replace with your Gemini key
genai.configure(api_key=API_KEY)

model = genai.GenerativeModel("gemini-1.5-flash")

# --- Page Config ---
st.set_page_config(page_title="NCLEX Smart Tutor", page_icon="💊", layout="centered")

st.title("💊 NCLEX Smart Tutor")
st.write("Practice NCLEX-style questions with AI-generated rationales.")

# --- User Input ---
topic = st.text_input("Enter a nursing topic (e.g., Heart Failure, Electrolytes):", "")
num_questions = st.slider("Number of questions:", 1, 5, 2)

if st.button("Generate Questions") and topic.strip():
    with st.spinner("Generating questions..."):
        prompt = f"""
        You are an NCLEX exam question generator.
        Create {num_questions} NCLEX-style multiple choice questions on the topic: {topic}.
        Each question should have:
        - A stem
        - Four answer choices (A, B, C, D)
        - One correct answer clearly marked
        - A rationale
        Format like this:

        Question:
        Choices:
        A. ...
        B. ...
        C. ...
        D. ...
        Answer: X
        Rationale: ...
        """

        response = model.generate_content(prompt)
        raw_output = response.text

        # --- Parse questions ---
        questions = re.split(r"Question:", raw_output)[1:]  # split by "Question:"
        for i, q in enumerate(questions, 1):
            st.markdown(f"### Q{i}:")
            st.markdown("Question:" + q.split("Choices:")[0].strip())

            choices_block = q.split("Choices:")[1].split("Answer:")[0].strip()
            answer_line = re.search(r"Answer:\s*([A-D])", q)
            rationale_match = re.search(r"Rationale:(.*)", q, re.DOTALL)

            correct_answer = answer_line.group(1) if answer_line else None
            rationale = rationale_match.group(1).strip() if rationale_match else "No rationale found."

            # Display choices with buttons
            for choice in ["A", "B", "C", "D"]:
                if f"{choice}." in choices_block:
                    if st.button(f"{choice}: " + choices_block.split(f"{choice}.")[1].split("\n")[0].strip(),
                                 key=f"{i}-{choice}"):
                        if choice == correct_answer:
                            st.success(f"✅ Correct! {rationale}")
                        else:
                            st.error(f"❌ Incorrect. Correct answer: {correct_answer}\n\nRationale: {rationale}")
            st.divider()
