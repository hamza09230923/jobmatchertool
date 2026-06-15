import requests
from fpdf import FPDF
import time
import json
import os
import sys

# 1. Generate a mock CV (PDF)
class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 15)
        self.cell(80)
        self.cell(30, 10, 'John Doe - Software Engineer', 0, 0, 'C')
        self.ln(20)

pdf = PDF()
pdf.add_page()
pdf.set_font('Arial', '', 12)

cv_text = """
John Doe
Location: London, UK | Email: john.doe@example.com | GitHub: github.com/johndoe

SUMMARY
Experienced Software Engineer with 5+ years of experience building scalable backend systems and robust data pipelines.

EXPERIENCE
Senior Software Engineer @ TechCorp
Jan 2020 - Present
- Designed and maintained backend pipelines using Python, Node.js, and AWS Lambda, supporting 60+ active users.
- Automated operational processes reducing manual workload by 40%.
- Integrated REST APIs for seamless data flow across microservices.

Software Engineer @ StartupInc
Jun 2017 - Dec 2019
- Developed full-stack applications using React, TypeScript, and Express.
- Managed database schemas in PostgreSQL and optimized queries for performance.
- Containerized applications using Docker and deployed to Kubernetes.

SKILLS
Python, JavaScript, TypeScript, Node.js, React, AWS, Docker, Kubernetes, PostgreSQL, SQL, REST APIs, Microservices.
"""

for line in cv_text.split('\n'):
    pdf.cell(200, 10, txt=line, ln=1, align='L')

pdf.output("mock_cv.pdf")
print("Generated mock_cv.pdf")


# 2. Define Job Description
job_description = """
Software Engineer (Backend)

About the role:
We are looking for a Software Engineer to join our core backend team. You will be responsible for designing, building, and maintaining scalable APIs and microservices.

Responsibilities:
- Build and maintain robust backend services using Python and FastAPI.
- Deploy applications using Docker and Kubernetes.
- Collaborate with frontend engineers to integrate RESTful APIs.
- Optimize database queries (PostgreSQL).
- Write clean, maintainable, and well-tested code.

Requirements:
- 3+ years of experience in software engineering.
- Proficiency in Python and at least one other language (e.g., JavaScript/TypeScript, Go).
- Experience with cloud platforms (AWS, GCP, or Azure).
- Familiarity with containerization (Docker, Kubernetes).
- Strong understanding of REST APIs and microservices architecture.
- Experience with SQL databases, especially PostgreSQL.

Nice to have:
- Experience with CI/CD pipelines.
- Knowledge of machine learning concepts.
"""

# 3. Setup backend interaction
BASE_URL = "http://127.0.0.1:8000"

def run_test():
    # Attempt to signup/login to get a token
    auth_data = {
        "email": "test@example.com",
        "password": "securepassword123!"
    }

    print("Attempting to sign up...")
    try:
        r = requests.post(f"{BASE_URL}/auth/signup", json=auth_data)
        if r.status_code == 409: # Already exists
            print("Account exists, logging in...")
            r = requests.post(f"{BASE_URL}/auth/login", json=auth_data)
        r.raise_for_status()
        token = r.json()["token"]
        print("Got token successfully.")
    except Exception as e:
        print(f"Failed to auth: {e}")
        if 'r' in locals():
            print(r.text)
        sys.exit(1)

    print("Uploading CV and analyzing...")
    with open("mock_cv.pdf", "rb") as f:
        files = {
            "resume": ("mock_cv.pdf", f, "application/pdf")
        }
        data = {
            "job_description": job_description,
            "job_source": "paste"
        }
        headers = {
            "Authorization": f"Bearer {token}"
        }

        try:
            r = requests.post(f"{BASE_URL}/analyze?debug=true", files=files, data=data, headers=headers)
            r.raise_for_status()

            result = r.json()
            print("\n" + "="*50)
            print("MATCH SCORE:", result.get("match_score"))
            print("MISSING KEYWORDS:", result.get("missing_keywords"))
            print("\nROLE FIT BREAKDOWN:")
            print(json.dumps(result.get("role_fit_breakdown", {}).get("skills_detail", {}), indent=2))
            print("="*50 + "\n")

            with open("result.json", "w") as out:
                json.dump(result, out, indent=2)
            print("Full result saved to result.json")

        except Exception as e:
            print(f"Failed to analyze: {e}")
            if 'r' in locals():
                print(r.text)
            sys.exit(1)

if __name__ == "__main__":
    run_test()
