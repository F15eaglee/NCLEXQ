# --- IMPORTS AND CONSTANTS ---
import os
import re
import json
import csv
import io
import urllib.parse as urlparse
import streamlit as st
import google.generativeai as genai

# --- Constants ---
MODEL_NAME = "gemini-1.5-flash"
SATA_KEYWORDS = {
    "sata", "select_all_that_apply", "select all that apply",
    "select_all", "select all that apply (sata)"
}
CSV_HEADER = [
    "question_type", "question", "option_a", "option_b", "option_c",
    "option_d", "option_e", "option_f", "correct_answer", "correct_answers",
    "rationale_a", "rationale_b", "rationale_c", "rationale_d", "rationale_e",
    "rationale_f", "youtube_search_term"
]
CSV_EXAMPLES = """
question_type,question,option_a,option_b,option_c,option_d,option_e,option_f,correct_answer,correct_answers,rationale_a,rationale_b,rationale_c,rationale_d,rationale_e,rationale_f,youtube_search_term
mcq,"Which action takes priority for a client with acute pulmonary edema?","Administer oxygen","Encourage oral fluids","Obtain daily weight","Teach low-sodium diet",,,A,,"Improves oxygenation immediately","Fluids may worsen overload","Weight is monitoring, not priority","Teaching is not priority in acute event",,,"pulmonary edema"
sata,"Select all initial nursing actions for suspected hypoglycemia.","Check blood glucose","Give long-acting insulin","Provide 15 g fast carbs","Reassess in 15 min","Call rapid response if LOC declines","Start IV access",,A;C;E;F,"Confirms diagnosis","Contraindicated; will worsen","Raises glucose quickly","Ensures treatment worked","Escalate if worsening","Allows dextrose/med access","hypoglycemia care"
""".strip()
ALL_LETTERS = [chr(c) for c in range(ord("A"), ord("H") + 1)]


# --- CONFIGURATION ---
def configure_api():
    """Retrieves API key and configures the Generative AI model."""
    try:
        api_key = st.secrets["API_KEY"]
    except (FileNotFoundError, KeyError):
        api_key = os.getenv("GOOGLE_API_KEY")

    if not api_key:
        st.error("Missing API key. Please set it in Streamlit secrets or as an environment variable (GOOGLE_API_KEY).")
        st.stop()
    
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(MODEL_NAME)

# --- PARSING LOGIC ---
def _clean_raw_text(text: str) -> str:
    """Removes markdown fences, BOM, and normalizes quotes from raw LLM output."""
    # Strip markdown code fences
    cleaned_text = re.sub(r"^```[a-zA-Z0-9]*\s*|\s*```$", "", text.strip())
    # Remove UTF-8 BOM and normalize special characters
    replacements = {
        "\ufeff": "", "\u201c": '"', "\u201d": '"', "\u2018": "'",
        "\u2019": "'", "\xa0": " "
    }
    for old, new in replacements.items():
        cleaned_text = cleaned_text.replace(old, new)
    return cleaned_text

def _read_csv_data(cleaned_text: str):
    """Finds the CSV header and yields each row as a dictionary."""
    lines = cleaned_text.splitlines()
    start_idx = -1
    # Find the header row, ignoring case and whitespace
    header_pattern = re.compile(r"\s*,\s*".join(CSV_HEADER), re.IGNORECASE)
    for i, line in enumerate(lines):
        if header_pattern.match(line.strip()):
            start_idx = i
            break
            
    if start_idx == -1:
        return []

    # Reconstruct the CSV text from the header onwards
    csv_content = "\n".join(lines[start_idx:])
    # Use DictReader for robust, column-name-based access
    reader = csv.DictReader(io.StringIO(csv_content), skipinitialspace=True)
    # Normalize header keys to lowercase for consistent access
    reader.fieldnames = [field.lower().strip() for field in reader.fieldnames]
    return reader

def _parse_csv_row(row: dict) -> dict | None:
    """Parses a single row dictionary from CSV into a structured question format."""
    question_text = row.get("question", "").strip()
    q_type_raw = row.get("question_type", "").strip().lower()
    is_sata = q_type_raw in SATA_KEYWORDS

    # Extract options and their corresponding letters
    options = {
        letter: row.get(f"option_{letter.lower()}", "").strip()
        for letter in ALL_LETTERS
        if row.get(f"option_{letter.lower()}", "").strip()
    }
    if not question_text or not options:
        return None

    # Extract correct answers
    correct_set = set()
    if is_sata:
        answers = row.get("correct_answers", "").strip()
        parts = re.split(r"[;,\s]+", answers)
        correct_set.update(p.upper() for p in parts if p.upper() in options)
    else:
        answer = row.get("correct_answer", "").strip().upper()
        if answer in options:
            correct_set.add(answer)
    
    if not correct_set:
        return None

    # Extract rationales
    rationales = {
        letter: row.get(f"rationale_{letter.lower()}", "").strip()
        for letter in options
    }

    # Extract YouTube search term
    search_term = row.get("youtube_search_term", "").strip()

    return {
        "q": question_text,
        "type": "sata" if is_sata else "mcq",
        "choices": [f"{L}. {options[L]}" for L in sorted(options.keys())],
        "correct_set": sorted(list(correct_set)),
        "rationales": rationales,
        "search_term": search_term,
    }

def parse_questions_from_csv(output_text: str) -> list:
    """Orchestrates the CSV parsing process."""
    cleaned_text = _clean_raw_text(output_text)
    if not cleaned_text:
        return []

    try:
        csv_reader = _read_csv_data(cleaned_text)
        parsed_questions = [_parse_csv_row(row) for row in csv_reader]
        return [q for q in parsed_questions if q] # Filter out None values
    except Exception as e:
        st.warning(f"CSV parsing failed: {e}. The format might be incorrect.")
        return []

# --- API AND PROMPT LOGIC ---
def build_prompt(topic, num_questions, difficulty, sata_percent) -> str:
    """Builds the prompt for the Gemini API call."""
    return f"""
# ROLE & GOAL
You are an expert NCLEX Test Development Specialist. Your goal is to generate {num_questions} high-quality, unique NCLEX-style practice questions on "{topic}" at a {difficulty} difficulty level.

# QUESTION SPECIFICATIONS
1.  **Question Mix**: Exactly {sata_percent}% of the questions must be **Select All That Apply (SATA)**. The rest must be standard **Multiple-Choice (MCQ)**.
2.  **Content**: Questions must test clinical judgment (application, analysis). Distractors must be plausible.
3.  **Choices**: MCQs must have 4 options (A-D). SATAs must have 5-6 options (A-E or A-F).

# OUTPUT FORMAT
Your entire response MUST be a valid CSV file, starting with the header. Do NOT include any other text or markdown.
**CSV Header**:
`{','.join(CSV_HEADER)}`

**Formatting Rules**:
-   **Rationales**: Provide a concise, instructive rationale for EVERY answer choice.
-   **YouTube Search Term**: Provide a simple, 2-4 word search term for finding a relevant educational video.
-   **MCQ Answers**: Put the single correct letter in the `correct_answer` column. `correct_answers` MUST be blank.
-   **SATA Answers**: Put the semicolon-separated list of correct letters (e.g., A;C;F) in the `correct_answers` column. `correct_answer` MUST be blank.

# GUARDRAIL
If "{topic}" is not a recognized nursing/medical subject, respond with ONLY the CSV header line.

# EXAMPLES
{CSV_EXAMPLES}
"""

def generate_questions_api(model, topic, num_q, difficulty, sata_pct):
    """Calls the Gemini API and parses the response."""
    with st.spinner(f"Generating {num_q} questions with Gemini... 🧠"):
        try:
            prompt = build_prompt(topic, num_q, difficulty, sata_pct)
            response = model.generate_content(prompt)
            output_text = response.text
            
            questions = parse_questions_from_csv(output_text)
            st.session_state.raw_output = output_text
            
            if not questions:
                st.error("❌ Gemini returned an empty or invalid response. Check the raw output below.")
                return []
            return questions

        except Exception as e:
            st.error(f"An error occurred while calling the API: {e}")
            return []

# --- UI COMPONENTS ---
def display_question(question, q_index):
    """Renders the current question and input widgets (radio or checkboxes)."""
    st.markdown(f"### Question {q_index + 1} of {len(st.session_state.questions)}")
    st.write(question["q"])

    if question["type"] == "mcq":
        # Using st.radio, which is ideal for single-choice questions
        selected_index = st.radio(
            "Choose one:",
            range(len(question["choices"])),
            format_func=lambda i: question["choices"][i],
            index=None,
            key=f"mcq_{q_index}"
        )
        if selected_index is not None:
            # Extract letter like 'A' from "A. Text"
            st.session_state.selected_letters = [question["choices"][selected_index][0]]
    else: # SATA
        st.write("Select all that apply:")
        selected = []
        for choice in question["choices"]:
            letter = choice