from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import base64
import os
import requests
import subprocess
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

    # Folder for repo
    repo_name = task.task
    folder = f"./repos/{repo_name}"
    os.makedirs(folder, exist_ok=True)

    print(f"🚀 Starting task: {repo_name}")

    # Save attachments
    for att in task.attachments:
        if att.url.startswith("data:"):
            content = base64.b64decode(att.url.split(",", 1)[1])
            file_path = os.path.join(folder, att.name)
            with open(file_path, "wb") as f:
                f.write(content)
            print(f"📎 Saved attachment: {file_path}")

    # Generate index.html using GPT
    generated_code = call_llm(task.brief)
    with open(os.path.join(folder, "index.html"), "w") as f:
        f.write(generated_code)
    print("✨ index.html generated from LLM")

    # Add README and LICENSE
    with open(os.path.join(folder, "README.md"), "w") as f:
        f.write(f"# {repo_name}\n\n{task.brief}\n\n## Checks\n" + "\n".join(f"- {c}" for c in task.checks) + "\n\n## License\nMIT\n")
    with open(os.path.join(folder, "LICENSE"), "w") as f:
        f.write("MIT License\n\nPermission is hereby granted...")
    print("📄 README.md and LICENSE created")

    # Git setup
    subprocess.run(["git", "init"], cwd=folder)
    subprocess.run(["git", "config", "user.name", GITHUB_USER], cwd=folder)
    subprocess.run(["git", "config", "user.email", f"{GITHUB_USER}@example.com"], cwd=folder)
    subprocess.run(["git", "add", "."], cwd=folder)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=folder)
    print("✅ Git repo initialized and committed")

    # Create GitHub repo
    response = requests.post(
        "https://api.github.com/user/repos",
        headers={"Authorization": f"token {GITHUB_TOKEN}"},
        json={"name": repo_name, "private": False}
    )
    print(f"🔍 GitHub repo creation response: {response.status_code} {response.text}")

    if response.status_code >= 300:
        raise HTTPException(status_code=500, detail=f"GitHub repo creation failed: {response.text}")

    # Push using token in remote URL
    push_url = f"https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{repo_name}.git"
    subprocess.run(["git", "remote", "add", "origin", push_url], cwd=folder)
    subprocess.run(["git", "branch", "-M", "main"], cwd=folder)
    result = subprocess.run(["git", "push", "-u", "origin", "main"], cwd=folder, capture_output=True, text=True)
    print(f"⬆️ Git push stdout: {result.stdout}")
    print(f"⚠️ Git push stderr: {result.stderr}")

    # Enable GitHub Pages
    pages_api = f"https://api.github.com/repos/{GITHUB_USER}/{repo_name}/pages"
    r = requests.post(
        pages_api,
        headers={"Authorization": f"token {GITHUB_TOKEN}"},
        json={"source": {"branch": "main", "path": "/"}}
    )
    print(f"🌍 GitHub Pages response: {r.status_code} {r.text}")

    if r.status_code >= 300:
        raise HTTPException(status_code=500, detail=f"GitHub Pages setup failed: {r.text}")

    # Final data
    commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=folder).decode().strip()
    pages_url = f"https://{GITHUB_USER}.github.io/{repo_name}/"

    # Notify evaluation URL
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
    print(f"📡 Evaluation URL response: {notify_resp.status_code} {notify_resp.text}")

    if notify_resp.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Evaluation notification failed: {notify_resp.text}")

    print("✅ Task completed successfully")
    return {"status": "done", "pages_url": pages_url}