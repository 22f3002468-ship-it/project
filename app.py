from fastapi import FastAPI, HTTPException
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

client = OpenAI(api_key=OPENAI_API_KEY)
app = FastAPI()

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

def call_llm(prompt):
    print("📨 Calling OpenAI with prompt...")
    detailed_prompt = f"""
        You're a professional frontend developer. Build a **minimal, working HTML/JS** web application **strictly** based on this task brief:

        \"\"\"
        {prompt}
        \"\"\"

        ✅ Ensure:
        - All required HTML elements and IDs/classes are present.
        - All logic runs in plain JavaScript (no frameworks unless stated).
        - Only include essential code.
        - Match expected output exactly (text, structure, casing).
        - If attachments are mentioned, read them via fetch with proper MIME type and process them.
        - Load required external libraries via CDN if requested.

        ⚠ DO NOT include explanations, markdown, or extra text—ONLY return the raw code (HTML+JS).
    """.strip()

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": detailed_prompt}]
    )
    print("✅ LLM Response received.")
    return response.choices[0].message.content

@app.post("/api")
async def handle_task(task: TaskRequest):
    print(f"📥 Received task: {task.task} (Round {task.round})")

    if task.secret != STUDENT_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    folder = f"./repos/{task.task}"
    os.makedirs(folder, exist_ok=True)
    print(f"📁 Created folder: {folder}")

    for att in task.attachments:
        if att.url.startswith("data:"):
            print(f"📎 Saving attachment: {att.name}")
            content = base64.b64decode(att.url.split(",", 1)[1])
            with open(os.path.join(folder, att.name), "wb") as f:
                f.write(content)

    print("⚙ Generating HTML/JS from LLM...")
    code = call_llm(task.brief)
    with open(os.path.join(folder, "index.html"), "w") as f:
        f.write(code)

    with open(os.path.join(folder, "README.md"), "w") as f:
        f.write(f"# {task.task}\n\n{task.brief}\n\n## Checks\n" + "\n".join(f"- {c}" for c in task.checks) + "\n\n## License\nMIT\n")
    with open(os.path.join(folder, "LICENSE"), "w") as f:
        f.write("MIT License\n\nPermission is hereby granted...")

    print("🔧 Initializing Git repository...")
    subprocess.run(["git", "init"], cwd=folder)
    subprocess.run(["git", "config", "user.name", GITHUB_USER], cwd=folder)
    subprocess.run(["git", "config", "user.email", f"{GITHUB_USER}@example.com"], cwd=folder)
    subprocess.run(["git", "add", "."], cwd=folder)
    subprocess.run(["git", "commit", "-m", f"Round {task.round} commit"], cwd=folder)

    print("📡 Creating GitHub repo...")
    response = requests.post(
        "https://api.github.com/user/repos",
        headers={"Authorization": f"token {GITHUB_TOKEN}"},
        json={"name": task.task, "private": False}
    )
    print(f"🔍 Repo creation response: {response.status_code} {response.text}")

    if response.status_code == 422 and "name already exists" in response.text:
        print("⚠ Repo already exists. Continuing with update.")
    elif response.status_code >= 300:
        raise HTTPException(status_code=500, detail="GitHub repo creation failed")

    print("🚀 Pushing to GitHub...")
    push_url = f"https://{GITHUB_USER}:{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{task.task}.git"
    subprocess.run(["git", "remote", "add", "origin", push_url], cwd=folder, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "branch", "-M", "main"], cwd=folder)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    print("🌐 Enabling GitHub Pages...")
    pages_api = f"https://api.github.com/repos/{GITHUB_USER}/{task.task}/pages"
    r = requests.post(
        pages_api,
        headers={"Authorization": f"token {GITHUB_TOKEN}"},
        json={"source": {"branch": "main", "path": "/"}}
    )
    print(f"📄 Pages response: {r.status_code} {r.text}")
    if r.status_code >= 300 and "already enabled" not in r.text.lower():
        raise HTTPException(status_code=500, detail=f"GitHub Pages setup failed: {r.text}")

    commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=folder).decode().strip()
    pages_url = f"https://{GITHUB_USER}.github.io/{task.task}/"

    print("📤 Notifying evaluation system...")
    notify_data = {
        "email": task.email,
        "task": task.task,
        "round": task.round,
        "nonce": task.nonce,
        "repo_url": f"https://github.com/{GITHUB_USER}/{task.task}",
        "commit_sha": commit_sha,
        "pages_url": pages_url
    }
    notify_resp = requests.post(task.evaluation_url, headers={"Content-Type": "application/json"}, json=notify_data)
    print(f"📬 Evaluation response: {notify_resp.status_code} {notify_resp.text}")

    if notify_resp.status_code != 200:
        print("❌ Evaluation system notification failed.")
        raise HTTPException(status_code=500, detail="Evaluation notification failed")

    return {"status": "done", "pages_url": pages_url}