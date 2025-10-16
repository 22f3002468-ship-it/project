from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import base64
import os
import requests
import subprocess
import csv
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_USER = os.getenv("GITHUB_USER")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
STUDENT_SECRET = os.getenv("STUDENT_SECRET")

# Init OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI()

# Pydantic models
class Attachment(BaseModel):
    name: str
    url: str

class TaskRequest(BaseModel):
    email: str
    secret: str
    task: str
    round: int
    nonce: str
    brief: str
    checks: list[str]
    evaluation_url: str
    attachments: list[Attachment]

# OpenAI helper
def call_llm(prompt):
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": f"Create a minimal HTML/JS web app based on this brief: {prompt}"}]
    )
    return response.choices[0].message.content

@app.post("/api")
async def handle_task(task: TaskRequest):
    if task.secret != STUDENT_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    repo_name = task.task
    folder = f"./repos/{repo_name}"
    os.makedirs(folder, exist_ok=True)

    for att in task.attachments:
        if att.url.startswith("data:"):
            content = base64.b64decode(att.url.split(",", 1)[1])
            with open(os.path.join(folder, att.name), "wb") as f:
                f.write(content)

    # Compute total sales if data.csv exists
    sum_sales = None
    csv_path = os.path.join(folder, "data.csv")
    if os.path.exists(csv_path):
        sum_sales = 0.0
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                sum_sales += float(row.get("sales", 0))
        print("✅ Computed sum_sales:", sum_sales)

    prompt = task.brief
    if sum_sales is not None:
        prompt += f"\nThe total sales is {sum_sales}. Use this value in computing and verifying."

    generated_code = call_llm(prompt)
    with open(os.path.join(folder, "index.html"), "w") as f:
        f.write(generated_code)

    with open(os.path.join(folder, "README.md"), "w") as f:
        f.write(f"# {repo_name}\n\n{task.brief}\n\n## Checks\n" + "\n".join(f"- {c}" for c in task.checks) + "\n\n## License\nMIT\n")
    with open(os.path.join(folder, "LICENSE"), "w") as f:
        f.write("MIT License\n\nPermission is hereby granted...")

    subprocess.run(["git", "init"], cwd=folder)
    subprocess.run(["git", "config", "user.name", GITHUB_USER], cwd=folder)
    subprocess.run(["git", "config", "user.email", f"{GITHUB_USER}@example.com"], cwd=folder)
    subprocess.run(["git", "add", "."], cwd=folder)
    commit_msg = f"Round {task.round} commit"
    subprocess.run(["git", "commit", "-m", commit_msg], cwd=folder)

    # Create GitHub repo if not exists
    response = requests.post(
        "https://api.github.com/user/repos",
        headers={"Authorization": f"token {GITHUB_TOKEN}"},
        json={"name": repo_name, "private": False}
    )
    if response.status_code == 422 and "name already exists" in response.text:
        print("Repo already exists. Using existing repo.")
    elif response.status_code >= 300:
        raise HTTPException(status_code=500, detail="GitHub repo creation failed")

    push_url = f"https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{repo_name}.git"
    result = subprocess.run(["git", "remote"], cwd=folder, capture_output=True, text=True)
    if "origin" not in result.stdout:
        subprocess.run(["git", "remote", "add", "origin", push_url], cwd=folder)
    subprocess.run(["git", "branch", "-M", "main"], cwd=folder)

    push = subprocess.run(["git", "push", "-u", "origin", "main"], cwd=folder, capture_output=True, text=True)
    print("Push stdout:", push.stdout)
    print("Push stderr:", push.stderr)
    if push.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Git push failed: {push.stderr}")

    pages_api = f"https://api.github.com/repos/{GITHUB_USER}/{repo_name}/pages"
    r = requests.post(
        pages_api,
        headers={"Authorization": f"token {GITHUB_TOKEN}"},
        json={"source": {"branch": "main", "path": "/"}}
    )
    print("Pages response:", r.status_code, r.text)
    if r.status_code >= 300 and "already exists" not in r.text:
        raise HTTPException(status_code=500, detail="GitHub Pages setup failed")

    commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=folder).decode().strip()
    pages_url = f"https://{GITHUB_USER}.github.io/{repo_name}/"

    notify_data = {
        "email": task.email,
        "task": task.task,
        "round": task.round,
        "nonce": task.nonce,
        "repo_url": f"https://github.com/{GITHUB_USER}/{repo_name}",
        "commit_sha": commit_sha,
        "pages_url": pages_url
    }

    notify_resp = requests.post(task.evaluation_url, headers={"Content-Type": "application/json"}, json=notify_data)
    if notify_resp.status_code != 200:
        raise HTTPException(status_code=500, detail="Evaluation notification failed")

    return {"status": "done", "pages_url": pages_url}