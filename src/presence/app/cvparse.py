"""CV → draft fields, locally. The draft is a suggestion the person edits and
confirms in the app; Presence never acts on it before that (charter rule 2)."""

from __future__ import annotations

import re

SKILL_WORDS = [
    "python",
    "java",
    "c++",
    "c#",
    "javascript",
    "typescript",
    "sql",
    "r",
    "go",
    "rust",
    "scala",
    "pytorch",
    "tensorflow",
    "scikit-learn",
    "pandas",
    "numpy",
    "spark",
    "docker",
    "kubernetes",
    "aws",
    "azure",
    "gcp",
    "fastapi",
    "django",
    "flask",
    "react",
    "node",
    "git",
    "linux",
    "mlflow",
    "airflow",
    "langchain",
    "rag",
    "llm",
    "nlp",
    "computer vision",
    "excel",
    "tableau",
    "power bi",
    "mongodb",
    "postgresql",
    "redis",
    "kafka",
    "terraform",
    "selenium",
    "pytest",
]
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
LINK_RE = re.compile(r"(?:github\.com|linkedin\.com/in)/[\w./-]+", re.I)


def extract_text(pdf_bytes: bytes) -> str:
    import pymupdf  # lazy: only when a CV is uploaded

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    return "\n".join(page.get_text("text", sort=True) for page in doc)


def guess_fields(text: str) -> dict[str, object]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    name = next(
        (
            ln
            for ln in lines[:6]
            if 1 < len(ln.split()) <= 4 and re.fullmatch(r"[A-Za-z'’ .-]+", ln)
        ),
        "",
    )
    email = EMAIL_RE.search(text)
    phone = PHONE_RE.search(text)
    low = text.lower()
    skills = [
        w for w in SKILL_WORDS if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", low)
    ]
    return {
        "name": name,
        "email": email.group(0) if email else "",
        "phone": phone.group(0).strip() if phone else "",
        "links": sorted(set(m.group(0) for m in LINK_RE.finditer(text))),
        "skills": skills,
        "text_chars": len(text),
    }
