import json
import os
import urllib.error
import urllib.request
from typing import Any

from dotenv import load_dotenv

load_dotenv()

_OPTION_LABELS = ("A", "B", "C", "D")
_QUESTION_PROMPT = """Create exactly 5 multiple-choice questions to assess a person's knowledge of {skill}.
Vary the topics and difficulty, and ensure every question has one unambiguous correct answer.
Return only a strict JSON array in this exact format:
[{{"question":"...","options":["A. ...","B. ...","C. ...","D. ..."],"correct_answer":"A"}}]
Each options array must contain exactly four strings labeled A through D. correct_answer must be
the single-letter label of the correct option. Do not include markdown or any text outside the JSON.
"""


def _question(
    prompt: str,
    options: tuple[str, str, str, str],
    correct_answer: str,
) -> dict[str, Any]:
    return {
        "question": prompt,
        "options": [
            f"{label}. {option}"
            for label, option in zip(_OPTION_LABELS, options)
        ],
        "correct_answer": correct_answer,
    }


_FALLBACK_QUIZZES: dict[str, list[dict[str, Any]]] = {
    "python": [
        _question("What does dict.get(key) return when key is absent and no default is given?", ("An empty string", "None", "KeyError", "The key itself"), "B"),
        _question("Which keyword is used to define a function in Python?", ("func", "function", "def", "define"), "C"),
        _question("What does the 'is' operator compare?", ("Object identity", "Numeric value only", "String length", "Variable names"), "A"),
        _question("Which syntax creates a list comprehension?", ("[x for x in items]", "(for x in items: x)", "{x -> items}", "<x for items>"), "A"),
        _question("What is the main benefit of using 'with open(...)' to read a file?", ("It compresses the file", "It closes the file when the block ends", "It makes the file read-only", "It skips decoding"), "B"),
    ],
    "java": [
        _question("Which declaration is the conventional Java application entry point?", ("public void start()", "static int main()", "public static void main(String[] args)", "public class main()"), "C"),
        _question("Which statement about Java String objects is correct?", ("They are immutable", "They can only contain numbers", "They are always mutable", "They cannot be compared"), "A"),
        _question("Which keyword declares that a class inherits from another class?", ("implements", "extends", "inherits", "super"), "B"),
        _question("Which collection is a resizable array implementation?", ("HashMap", "HashSet", "ArrayList", "TreeMap"), "C"),
        _question("What must Java code generally do with a checked exception?", ("Ignore it", "Handle it or declare it", "Convert it to a boolean", "Make the method static"), "B"),
    ],
    "sql": [
        _question("Which SQL clause selects which columns to return?", ("SELECT", "WHERE", "GROUP BY", "ORDER BY"), "A"),
        _question("Which clause filters rows before grouping and aggregation?", ("HAVING", "WHERE", "ORDER BY", "LIMIT"), "B"),
        _question("What does a primary key require for each row?", ("A unique, non-null value", "A value shared by all rows", "A text value", "A foreign table"), "A"),
        _question("Which join returns rows with matching values in both tables?", ("CROSS JOIN", "LEFT JOIN", "INNER JOIN", "FULL OUTER JOIN"), "C"),
        _question("What does COUNT(*) count?", ("Only non-null values in the first column", "The number of rows", "The number of tables", "Distinct values in every column"), "B"),
    ],
    "html": [
        _question("Which element creates a hyperlink?", ("<link>", "<a>", "<href>", "<url>"), "B"),
        _question("Which attribute provides alternative text for an image?", ("title", "src", "alt", "caption"), "C"),
        _question("Which element represents the highest-level heading?", ("<head>", "<h6>", "<heading>", "<h1>"), "D"),
        _question("Which semantic element is intended for a group of navigation links?", ("<nav>", "<aside>", "<footer>", "<section>"), "A"),
        _question("Which input type is intended for email addresses?", ("text-email", "mail", "email", "address"), "C"),
    ],
    "css": [
        _question("What does box-sizing: border-box do?", ("Includes padding and border in the declared width and height", "Removes all element borders", "Makes an element position: fixed", "Sets width to the viewport"), "A"),
        _question("Which display value is designed for one-dimensional flexible layouts?", ("block", "flex", "table", "contents"), "B"),
        _question("Which selector normally has the highest specificity?", ("A class selector", "An element selector", "An ID selector", "A universal selector"), "C"),
        _question("What kind of selector is :hover?", ("Pseudo-class", "Attribute selector", "Combinator", "Pseudo-element"), "A"),
        _question("Which at-rule applies styles conditionally based on media features?", ("@supports", "@font-face", "@keyframes", "@media"), "D"),
    ],
    "javascript": [
        _question("Which operator compares both value and type without coercion?", ("==", "===", "=", "!="), "B"),
        _question("What does Array.prototype.map() return?", ("A new array of transformed elements", "The first matching element", "A boolean", "The original array sorted in place"), "A"),
        _question("Which keyword declares a block-scoped variable that can be reassigned?", ("const", "varies", "let", "static"), "C"),
        _question("What does an async function always return?", ("A Promise", "A generator", "A callback", "A synchronous value only"), "A"),
        _question("If an object is assigned to a const variable, what is allowed?", ("Reassigning the variable", "Changing a property of the object", "Changing const to let", "Deleting the variable"), "B"),
    ],
}


def _validate_questions(questions: Any) -> list[dict[str, Any]]:
    if not isinstance(questions, list) or len(questions) != 5:
        raise ValueError("Quiz response must contain exactly five questions")

    validated: list[dict[str, Any]] = []
    for question in questions:
        if not isinstance(question, dict) or set(question) != {
            "question",
            "options",
            "correct_answer",
        }:
            raise ValueError("Each question must contain question, options, and correct_answer")

        text = question["question"]
        options = question["options"]
        answer = question["correct_answer"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Question text must be a non-empty string")
        if (
            not isinstance(options, list)
            or len(options) != 4
            or any(
                not isinstance(option, str)
                or not option.startswith(f"{label}. ")
                for label, option in zip(_OPTION_LABELS, options)
            )
        ):
            raise ValueError("Each question must have four options labeled A through D")
        if answer not in _OPTION_LABELS:
            raise ValueError("correct_answer must be one of A, B, C, or D")

        validated.append(
            {
                "question": text.strip(),
                "options": [option.strip() for option in options],
                "correct_answer": answer,
            }
        )
    return validated


def _parse_questions(content: str) -> list[dict[str, Any]]:
    return _validate_questions(json.loads(content))


def _generate_with_gemini(skill: str, api_key: str) -> list[dict[str, Any]]:
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        generation_config={
            "response_mime_type": "application/json",
            "temperature": 0.4,
        },
    )
    response = model.generate_content(_QUESTION_PROMPT.format(skill=skill))
    content = response.text
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Gemini returned no quiz content")
    return _parse_questions(content)


def generate_quiz(skill: str) -> list[dict[str, Any]]:
    """Generate five multiple-choice questions using AI or a built-in fallback."""
    if not isinstance(skill, str) or not skill.strip():
        raise ValueError("skill must be a non-empty string")

    normalized_skill = skill.strip()
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        try:
            return _generate_with_gemini(normalized_skill, api_key)
        except Exception:
            pass

    fallback = _FALLBACK_QUIZZES.get(normalized_skill.casefold())
    if fallback is None:
        raise RuntimeError(
            f"Could not generate a quiz for {normalized_skill!r}; no built-in fallback quiz is available."
        )

    return [
        {
            "question": question["question"],
            "options": list(question["options"]),
            "correct_answer": question["correct_answer"],
        }
        for question in fallback
    ]