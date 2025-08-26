import streamlit as st
import google.generativeai as genai
import re
import os
    sata_template = """
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
                                                try:
                                                    mcq_template = """
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
                                        },
                                        "resource_link": "https://www.example.com/resource"
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
                                        },
                                        "resource_link": "https://www.example.com/resource"
                                    }
                                    """.strip()

                                                        # Build the model prompt
                                                        prompt = "\n".join([
                                                            f"You are a Nursing school Instructor preparing students for the NCLEX exam.",
                                                            f"Create {num_questions} {difficulty} NCLEX-style questions on {topic} with answers and rationales.",
                                                            f"{question_type_percent}% of questions should be select_all_that_apply with 6 answer choices (A-F);",
                                                            "the rest should be multiple_choice with 4 answer choices (A-D).",
                                                            "If anything unrelated to nursing is prompted, ignore it.",
                                                            "Output valid JSON only. Do not include any text or Markdown code fences before or after the JSON.",
                                                            'The top-level JSON must be an object with a single key "questions" containing an array of question objects.',
                                                            "For multiple_choice items, use this template:",
                                                            mcq_template,
                                                            "For select_all_that_apply items, use this template:",
                                                            sata_template,
                                                            "For each question, add a 'resource_link' field with a reputable online resource (official NCLEX, Khan Academy, or YouTube) for further study on the question topic."
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

            # Add instruction for resource_link
            prompt += "\nFor each question, add a 'resource_link' field with a reputable online resource (official NCLEX, Khan Academy, or YouTube) for further study on the question topic."
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

        def parse_questions(output_text):
            parsed = []

            # Strip possible Markdown code fences
            txt = output_text.strip()
            if txt.startswith("```"):
                txt = re.sub(r"^```[a-zA-Z0-9]*\s*", "", txt)
                txt = re.sub(r"\s*```$", "", txt)

                data = json.loads(txt)

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

                # Extract resource_link if present
                resource_link = q.get("resource_link", "").strip()

                if question_text and choices and correct_set:
                    parsed.append({
                        "q": question_text,
                        "type": qtype,
                        "choices": choices,          # ["A. ...", "B. ...", ...]
                        "correct_set": sorted(list(correct_set)),  # keep stable order
                        "rationales": rationales_map,  # { "A": "...", ... }
                        "resource_link": resource_link
                    })

            return parsed
                st.info(f"{L}. {text}\n\n💡 {expl}" if expl else f"{L}. {text}")

        st.write(f"📊 Score: {st.session_state.score}/{q_index+1}")

        # --- Learn More: External Resource Link from Gemini ---
        resource_link = question.get("resource_link", "")
        st.markdown("#### 📚 Learn More")
        if resource_link:
            st.markdown(f"- [Recommended Resource]({resource_link})")
        else:
            st.info("No specific resource link provided for this question.")

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
