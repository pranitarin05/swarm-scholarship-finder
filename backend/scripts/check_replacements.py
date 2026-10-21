"""Test candidate replacement URLs for dead seeds."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.tools.fetch import fetch_page

CANDIDATES = [
    ("Erasmus Mundus (org)", "https://www.erasmus-mundus.org/"),
    ("Erasmus Plus EJMS", "https://erasmus-plus.ec.europa.eu/opportunities/individuals/students/erasmus-mundus-joint-masters-emejms"),
    ("UofT SGS funding alt", "https://www.sgs.utoronto.ca/about/financial-assistance/"),
    ("UofT tuition", "https://www.utoronto.ca/finance-financial-information/tuition-fees"),
    ("UBC grad funding", "https://www.grad.ubc.ca/prospective-students/graduate-degree-programs/costs-tuition"),
    ("Waterloo funding", "https://uwaterloo.ca/future-students/graduate/funding"),
    ("Waterloo awards", "https://uwaterloo.ca/graduate-studies/"),
    ("NTU root", "https://www.ntu.edu.sg/"),
    ("NTU admissions", "https://www.ntu.edu.sg/admissions/graduate-admissions"),
    ("TUM studies", "https://www.tum.de/en/studies"),
    ("TUM fees", "https://www.tum.de/en/studies/fees-and-financial-aid"),
    ("Vanier NSERC", "https://www.nserc-crsng.gc.ca/students-etudiants/pg-cs/vanier-vanier_eng.asp"),
    ("SINGA a-star", "https://www.a-star.edu.sg/SINGA"),
    ("SINGA root", "https://www.a-star.edu.sg/"),
    ("Ontario Tech grad", "https://uoit.ca/future-students/graduate/"),
    ("McGill grad", "https://www.mcgill.ca/gradapplicants/"),
    ("Uni Bonn master", "https://www.uni-bonn.de/en/studying/degree-programs"),
    ("Uni Heidelberg master", "https://www.uni-heidelberg.de/en/study/subject-catalogue"),
]


async def main() -> None:
    for name, url in CANDIDATES:
        page = await fetch_page(url)
        print(f"{page.status:3} {'OK ' if page.ok else 'BAD'} {len(page.text):5}  {name}")


if __name__ == "__main__":
    asyncio.run(main())
