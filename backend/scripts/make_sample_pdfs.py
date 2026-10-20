"""Generate fictional sample files for the demo persona: Rahul Sharma.

Usage: python backend/scripts/make_sample_pdfs.py
Output: data/sample_rahul/resume.pdf, data/sample_rahul/marksheet.pdf
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "sample_rahul"

RESUME_SECTIONS: list[tuple[str, str | list]] = [
    ("Contact", "Rahul Sharma | Jalandhar, Punjab, India | rahul.sharma.cse@example.com | "
                "+91 90000 00000 | github.com/rahul-sharma-ai"),
    ("Education", "B.Tech Computer Science and Engineering, Final Year (2022-2026)\n"
                  "Lovely Professional University, Jalandhar, Punjab\n"
                  "CGPA: 8.2 / 10"),
    ("Skills", "Python, PyTorch, scikit-learn, Pandas, NumPy, FastAPI, Docker, Git, "
               "SQL, LangChain, Hugging Face, OpenCV"),
    ("Projects", [
        "Crop Disease Detector - CNN classifier for leaf diseases (94% accuracy on "
        "PlantVillage subset). PyTorch, FastAPI, Docker. github.com/rahul-sharma-ai/crop-disease-detector",
        "RAG College Assistant - Retrieval-augmented QA chatbot over university "
        "handbook PDFs using LlamaIndex + Groq. github.com/rahul-sharma-ai/rag-college-assistant",
        "Loan Default Prediction - Gradient boosting model, 0.87 AUC; feature "
        "importance study. Scikit-learn, Pandas.",
    ]),
    ("Experience", "Machine Learning Intern, KritiKal Solutions (Jun 2025 - Aug 2025)\n"
                   "- Built a document QA pipeline (OCR + embeddings) used by 3 internal teams\n"
                   "- Reduced inference latency 40% by batching GPU requests"),
    ("Achievements", "- Smart India Hackathon 2025 finalist (team of 6)\n"
                     "- Kaggle: Titanic top 8%, Spaceship-Titanic bronze medal\n"
                     "- NPTEL: Machine Learning (85%), Programming in Java (82%)"),
    ("Languages", "English (professional), Hindi (native), Punjabi (native)\n"
                  "IELTS: not yet taken | GRE: not taken"),
    ("Statement", "Final-year CSE student from Jalandhar with strong AI projects and "
                  "internship experience. Seeking fully funded Master's programs in "
                  "Canada, Germany or Singapore in AI/ML; family cannot afford "
                  "full tuition fees."),
]

MARKSHEET_ROWS = [
    ("Semester", "Course", "Marks", "CGPA"),
    ("1", "Maths I, Physics, Programming, Eng. Graphics", "820/1000", "8.2"),
    ("2", "Maths II, Chemistry, DSA, Electrical", "845/1000", "8.4"),
    ("3", "Discrete Maths, OOP, DBMS, OS", "838/1000", "8.4"),
    ("4", "Probability, Computer Networks, Software Eng", "856/1000", "8.6"),
    ("5", "AI Fundamentals, ML, Automata, Web Tech", "861/1000", "8.6"),
    ("6", "Deep Learning, Big Data, Cloud Computing, Mini Project", "872/1000", "8.7"),
    ("7", "Computer Vision, NLP, Elective I, Project I", "849/1000", "8.5"),
]


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontSize=16, spaceAfter=4),
        "h": ParagraphStyle("h", parent=base["Heading2"], fontSize=11,
                            textColor=colors.HexColor("#1d4ed8"), spaceBefore=8),
        "body": ParagraphStyle("b", parent=base["BodyText"], fontSize=9, leading=12),
        "small": ParagraphStyle("s", parent=base["BodyText"], fontSize=8, leading=10),
    }


def build_resume(path: Path) -> None:
    styles = _styles()
    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            leftMargin=1.5 * cm, rightMargin=1.5 * cm)
    story: list = [Paragraph("Rahul Sharma", styles["title"]),
                   Paragraph("B.Tech CSE (Final Year) | Aspiring AI/ML Graduate Student",
                             styles["small"]), Spacer(1, 6)]
    for heading, content in RESUME_SECTIONS:
        story.append(Paragraph(heading, styles["h"]))
        if isinstance(content, list):
            for item in content:
                story.append(Paragraph(f"- {item}", styles["body"]))
        else:
            for line in content.split("\n"):
                story.append(Paragraph(line, styles["body"]))
    doc.build(story)


def build_marksheet(path: Path) -> None:
    styles = _styles()
    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            leftMargin=1.5 * cm, rightMargin=1.5 * cm)
    story: list = [
        Paragraph("Lovely Professional University", styles["title"]),
        Paragraph("Transcript of Academic Record - Rahul Sharma, B.Tech CSE, "
                  "Roll No. 22104517, Session 2022-2026", styles["small"]),
        Spacer(1, 10),
    ]
    table = Table(MARKSHEET_ROWS, colWidths=[2 * cm, 9 * cm, 3 * cm, 2.5 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dbeafe")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(table)
    story.append(Spacer(1, 8))
    story.append(Paragraph("Overall CGPA: 8.5 / 10 (Aggregate: 85.0%)", styles["h"]))
    story.append(Paragraph("Issued: 15 May 2026 | Controller of Examinations | "
                           "This is a fictional sample document for demo purposes.",
                           styles["small"]))
    doc.build(story)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    build_resume(OUT_DIR / "resume.pdf")
    build_marksheet(OUT_DIR / "marksheet.pdf")
    print(f"sample PDFs written to {OUT_DIR}")


if __name__ == "__main__":
    main()
