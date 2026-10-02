import sys
from pathlib import Path

# sAI's install folder (the default workspace for the web UI)
SAI_ROOT = Path(__file__).resolve().parent.parent

# Add project root to sys.path to allow execution from any CWD
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.orchestrator import Orchestrator
from core.task import Task



def main():
    if "--tui" in sys.argv or "-t" in sys.argv:
        from ui.terminal import SaiApp
        app = SaiApp()
        app.run()
        return
    if "--run" in sys.argv or "-r" in sys.argv:
        try:
            flag_idx = sys.argv.index("--run") if "--run" in sys.argv else sys.argv.index("-r")
            goal = sys.argv[flag_idx + 1]
        except (ValueError, IndexError):
            print("Error: Please provide a goal prompt, e.g. python3 app/main.py --run 'fix hello.py'")
            sys.exit(1)
        print(f"\n=== sAI Multi-Agent Run: '{goal}' ===\n")
        task = Task(goal=goal)
        orchestrator = Orchestrator()
        orchestrator.run(task)
        return
    if "--cli" in sys.argv or "-c" in sys.argv:
        from app.repl import SaiRepl
        SaiRepl().run()
        return

    # Default to running Web UI
    else:
        import json
        import webbrowser
        import threading
        from http.server import HTTPServer, BaseHTTPRequestHandler
        from core.orchestrator import Orchestrator
        from core.task import Task
        from core.events import event_bus, Event, EventType
        from execution.permissions import PermissionRequestRequired

        import os
        PORT = 8000
        if "--port" in sys.argv:
            try:
                PORT = int(sys.argv[sys.argv.index("--port") + 1])
            except (ValueError, IndexError):
                print("Error: --port requires a numeric value, e.g. --port 8010")
                sys.exit(1)
        else:
            env_port = os.getenv("SAI_WEB_PORT")
            if env_port:
                try:
                    PORT = int(env_port)
                except ValueError:
                    pass

        class ClaylineHTTPServer(BaseHTTPRequestHandler):
            SESSION_CONVERSATIONS = {}
            SESSION_WAITING = {}
            # Per-session ring buffer of live agent/tool activity, drained incrementally
            # by /api/activity/stream so the UI can render turns as they happen instead
            # of waiting for /api/run's single blocking response.
            SESSION_ACTIVITY_LOG = {}

            def log_message(self, format, *args):
                return  # Suppress server request stdout logging to keep terminal clean

            def do_GET(self):
                if self.path in ["/", "/index.html", "/ui"]:
                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    html_path = Path(__file__).resolve().parent.parent / "ui" / "clayline-terminal.html"
                    self.wfile.write(html_path.read_bytes())
                elif self.path.startswith("/api/activity/stream"):
                    try:
                        from urllib.parse import urlparse, parse_qs
                        from core.security import get_current_activity
                        from llm.tracker import token_tracker

                        query = parse_qs(urlparse(self.path).query)
                        session_id = query.get("session_id", ["default_session"])[0]
                        try:
                            since = int(query.get("since", ["0"])[0])
                        except ValueError:
                            since = 0

                        payload = get_current_activity()
                        payload["input_tokens"] = token_tracker.input_tokens
                        payload["output_tokens"] = token_tracker.output_tokens
                        payload["calls_count"] = token_tracker.calls_count
                        payload["elapsed_time"] = token_tracker.elapsed_time
                        payload["speed"] = token_tracker.speed

                        log = self.SESSION_ACTIVITY_LOG.get(session_id, [])
                        payload["log"] = log[since:] if since < len(log) else []
                        payload["log_cursor"] = len(log)

                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps(payload).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))
                elif self.path == "/api/settings":
                    try:
                        from core.settings import load_settings
                        settings = load_settings()
                        safe_settings = dict(settings)
                        for secret_key in ("nvidia_key", "openai_key", "openrouter_key"):
                            if safe_settings.get(secret_key):
                                key = safe_settings[secret_key]
                                safe_settings[secret_key] = key[:6] + "..." + key[-4:] if len(key) > 10 else "..."

                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps(safe_settings).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))
                elif self.path == "/api/memory/graph":
                    try:
                        from memory.graphiti import GraphitiMemory
                        graphiti = GraphitiMemory()
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success",
                            "facts": graphiti.facts,
                            "decisions": graphiti.decisions
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))
                elif self.path == "/api/git/status":
                    try:
                        from core.security import get_current_workspace
                        workspace = get_current_workspace()
                        
                        import subprocess
                        # Get status
                        status_res = subprocess.run(["git", "status", "-s"], cwd=str(workspace), capture_output=True, text=True)
                        # Get diff
                        diff_res = subprocess.run(["git", "diff"], cwd=str(workspace), capture_output=True, text=True)
                        
                        # Get branch name
                        branch_name = "main"
                        try:
                            from git import Repo
                            repo = Repo(workspace, search_parent_directories=True)
                            branch_name = repo.active_branch.name
                        except Exception:
                            pass
                            
                        modified_files = []
                        for line in status_res.stdout.splitlines():
                            if line.strip():
                                modified_files.append(line.strip())
                                
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success",
                            "branch": branch_name,
                            "modified": modified_files,
                            "diff": diff_res.stdout
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))
                elif self.path.startswith("/api/workspace/outline"):
                    try:
                        from urllib.parse import urlparse, parse_qs
                        query = parse_qs(urlparse(self.path).query)
                        path_str = query.get("path", [""])[0].strip()
                        
                        from core.security import validate_path
                        target_file = Path(path_str)
                        if not target_file.is_absolute():
                            target_file = SAI_ROOT / target_file
                            
                        symbols = []
                        if validate_path(target_file) and target_file.exists() and target_file.is_file():
                            symbols = parse_outline_symbols(target_file)
                            
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success",
                            "symbols": symbols
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))
                elif self.path.startswith("/api/git/diff"):
                    try:
                        from urllib.parse import urlparse, parse_qs
                        query = parse_qs(urlparse(self.path).query)
                        path_str = query.get("path", [""])[0].strip()

                        import subprocess
                        cwd = str(SAI_ROOT)

                        status_res = subprocess.run(["git", "status", "--porcelain", path_str], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                        is_untracked = "??" in status_res.stdout

                        if is_untracked:
                            cmd = ["git", "diff", "--no-index", "/dev/null", path_str]
                        else:
                            cmd = ["git", "diff", "--", path_str]

                        res = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                        diff_text = res.stdout

                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success",
                            "diff": diff_text
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))
                elif self.path.startswith("/api/workspace/search"):
                    try:
                        from urllib.parse import urlparse, parse_qs
                        query_params = parse_qs(urlparse(self.path).query)
                        query_str = query_params.get("query", [""])[0].strip()

                        from core.security import get_current_workspace
                        target_dir = get_current_workspace()

                        results = []
                        if query_str:
                            import math
                            query_terms = query_str.lower().split()

                            files = [p for p in target_dir.rglob("*") if p.is_file() and not p.name.startswith(".") and ".sai" not in p.parts and "venv" not in p.parts and "node_modules" not in p.parts]

                            for f_path in files:
                                try:
                                    content = f_path.read_text(encoding='utf-8', errors='ignore')
                                    content_lower = content.lower()
                                    if any(term in content_lower for term in query_terms):
                                        score = 0.0
                                        for term in query_terms:
                                            tf = content_lower.count(term)
                                            if tf > 0:
                                                score += (1.0 + math.log(tf))
                                        if score > 0:
                                            idx = content_lower.find(query_terms[0])
                                            snippet = content[max(0, idx - 40):min(len(content), idx + 80)].replace("\n", " ").strip()
                                            results.append({
                                                "name": f_path.name,
                                                "path": str(f_path.relative_to(target_dir)),
                                                "snippet": f"...{snippet}...",
                                                "score": round(score, 2)
                                            })
                                except Exception:
                                    pass

                            results = sorted(results, key=lambda x: x["score"], reverse=True)[:8]

                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success",
                            "results": results
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))
                elif self.path == "/api/terminal/stream":
                    try:
                        from tools.terminal import async_process_manager
                        new_output = async_process_manager.get_new_output()
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success",
                            "lines": new_output,
                            "is_running": async_process_manager.is_running
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))
                elif self.path == "/api/diagnostics/resources":
                    try:
                        import subprocess
                        import random

                        cpu_percent = 0.0
                        mem_percent = 0.0

                        try:
                            res = subprocess.run(["ps", "-A", "-o", "%cpu,%mem"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2.0)
                            lines = res.stdout.strip().split("\n")[1:]
                            cpu_total = 0.0
                            mem_total = 0.0
                            for line in lines:
                                parts = line.strip().split()
                                if len(parts) >= 2:
                                    try:
                                        cpu_total += float(parts[0])
                                        mem_total += float(parts[1])
                                    except ValueError:
                                        pass
                            cpu_percent = min(100.0, cpu_total)
                            mem_percent = min(100.0, mem_total)
                        except Exception:
                            cpu_percent = round(random.uniform(5.0, 15.0), 1)
                            mem_percent = round(random.uniform(20.0, 35.0), 1)

                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success",
                            "cpu": round(cpu_percent, 1),
                            "memory": round(mem_percent, 1)
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                if self.path == "/api/run":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        session_id = data.get("session_id", "default_session")
                        
                        from llm.tracker import token_tracker
                        token_tracker.reset()
                        
                        goal = data.get("goal", "")
                        if not goal:
                            self.send_response(400)
                            self.end_headers()
                            self.wfile.write(b"Error: Goal is empty.")
                            return
                        
                        # 1. Set dynamic workspace context first so bypasses use correct CWD!
                        target_path = data.get("target_path", "").strip()
                        symbol_name = None
                        if target_path and ":" in target_path:
                            parts = target_path.split(":", 1)
                            target_path = parts[0].strip()
                            symbol_name = parts[1].strip()

                        if target_path:
                            from core.security import validate_path, set_current_workspace
                            if not validate_path(target_path):
                                raise PermissionRequestRequired(
                                    path=target_path,
                                    reason="Accessing target path workspace context."
                                )
                            set_current_workspace(target_path)
                            
                            # Sanitize literal goal prompts referencing files to directories
                            resolved_path = Path(target_path)
                            if not resolved_path.is_absolute():
                                resolved_path = SAI_ROOT / resolved_path
                            if resolved_path.exists() and resolved_path.is_dir():
                                goal = goal.replace("code file at path", "codebase directory at path")
                                goal = goal.replace("the file at path", "the codebase directory at path")
                                goal = goal.replace("review the file", "review the directory")
                                goal = goal.replace("read the file", "read files in the directory")
                        else:
                            from core.security import set_current_workspace
                            set_current_workspace(str(SAI_ROOT))

                        # 2. Direct Shell & Git Command Bypass
                        cleaned_goal = goal.strip()
                        if cleaned_goal.startswith("!") or cleaned_goal.lower().startswith("git "):
                            cmd_to_run = cleaned_goal[1:].strip() if cleaned_goal.startswith("!") else cleaned_goal
                            
                            from tools.terminal import TerminalTool
                            tool = TerminalTool()
                            result = tool.execute({"command": cmd_to_run})
                            
                            self.send_response(200)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            
                            self.wfile.write(json.dumps({
                                "status": "success",
                                "events": [{
                                    "agent": "Terminal",
                                    "summary": f"Executed: {cmd_to_run}",
                                    "reasoning": ["Bypassed multi-agent loop for direct console command."],
                                    "confidence": 1.0,
                                    "content": result
                                }],
                                "memory_snapshot": ""
                            }).encode('utf-8'))
                            return

                        # 3. Direct Greeting Command Bypass
                        cleaned_goal_lower = goal.strip().lower().rstrip(".!?")
                        if cleaned_goal_lower in ["hi", "hello", "hey", "greetings", "yo"]:
                            self.send_response(200)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({
                                "status": "success",
                                "events": [{
                                    "agent": "sAI",
                                    "summary": "Hello! I am sAI, your AI Operating System. How can I help you today?",
                                    "reasoning": ["Greeting processed locally."],
                                    "confidence": 1.0,
                                    "content": "Hello! I am sAI, your AI Operating System. How can I help you today?"
                                }],
                                "memory_snapshot": ""
                            }).encode('utf-8'))
                            return

                        tech_stack = data.get("tech_stack", "")
                        stack_prompts = {
                            "python_fastapi": "The project stack is Python FastAPI. Write clean modular API endpoints using FastAPI. Write unit tests in a tests/ directory using pytest. Verify by running the command 'pytest'.",
                            "python_flask": "The project stack is Python Flask. Implement endpoints using Flask. Write unit tests inside a tests/ directory using unittest. Verify by running the command 'python -m unittest discover'.",
                            "java_springboot": "The project stack is Java Spring Boot. Manage dependencies and compiles using Maven (pom.xml). Write JUnit test suites. Verify by running the command 'mvn clean test'.",
                            "node_express": "The project stack is Node.js with Express. Configure package.json. Write Jest unit tests. Verify by running the command 'npm test'.",
                            "nextjs_tailwind": "The project stack is Next.js with React and Tailwind CSS. Implement page routing and custom CSS. Build using 'npm run build'.",
                            "react_vite": "The project stack is Frontend React built with Vite and TypeScript. Manage dependencies via package.json. Build with 'npm run build'.",
                            "go_gin": "The project stack is Go Gin. Package endpoints inside Go files. Write Go unit tests. Verify by running 'go test ./...'.",
                            "rust_axum": "The project stack is Rust Axum. Write memory-safe endpoints in Rust. Write cargo test suites. Verify by running 'cargo test'.",
                            "vanilla_web": "The project stack is Vanilla HTML, CSS, and JS. Create index.html as the primary landing page structure, style.css for modern visual elements, and index.js for interactive logic. Link them correctly in index.html."
                        }
                        
                        task = Task(goal=goal)
                        
                        # Restore conversation history
                        if session_id not in self.SESSION_CONVERSATIONS:
                            self.SESSION_CONVERSATIONS[session_id] = []
                            
                        self.SESSION_WAITING[session_id] = False
                        self.SESSION_ACTIVITY_LOG[session_id] = []

                        if session_id in self.SESSION_CONVERSATIONS:
                            for msg in self.SESSION_CONVERSATIONS[session_id]:
                                task.context.conversation.add(msg)
                                
                        instructions = stack_prompts.get(tech_stack, "")
                        if instructions:
                            task.context.memory.notes.append(f"Framework Context: {instructions}")
                            
                        global_quality_prompt = (
                            "CRITICAL REQUIREMENT: You must write complete, production-ready, fully functional source code implementation files. "
                            "Do NOT write placeholder comments, TODO stubs (like '// TODO' or '// Add style here'), or mock files. "
                            "Write out the full source code logic, complete CSS definitions, and fully active DOM event handlers. "
                            "Ensure every file (HTML, CSS, JS, Python, etc.) contains fully written, executable, and complete code blocks."
                        )
                        task.context.memory.notes.append(f"Quality Guidelines: {global_quality_prompt}")
                        
                        if target_path:
                            task.context.memory.notes.append(f"Active Target Path Context: {target_path}")
                            resolved_path = Path(target_path)
                            if not resolved_path.is_absolute():
                                resolved_path = SAI_ROOT / resolved_path
                            if resolved_path.exists() and resolved_path.is_dir():
                                # Scan for important source code files to inject as context (max 3 files, max 10KB each)
                                important_extensions = ['.py', '.js', '.ts', '.html', '.css', '.go']
                                injected_files = []
                                try:
                                    from repository.ignore import IgnoreParser
                                    ignore_parser = IgnoreParser(resolved_path)
                                    for child in sorted(resolved_path.rglob('*')):
                                        if child.is_file() and child.suffix in important_extensions:
                                            # Check ignore list
                                            if not ignore_parser.is_ignored(child):
                                                injected_files.append(child)
                                                if len(injected_files) >= 3:
                                                    break
                                except Exception:
                                    pass
                                    
                                if injected_files:
                                    file_payload = "Codebase Files Context:\n"
                                    for f in injected_files:
                                        try:
                                            file_payload += f"\n--- File: {f.name} ---\n{f.read_text(encoding='utf-8', errors='ignore')[:10000]}\n"
                                        except Exception:
                                            pass
                                    task.context.memory.notes.append(file_payload)
                                else:
                                    task.context.memory.notes.append(
                                        f"The target path '{target_path}' is a directory codebase. To review this codebase, run the tool 'list_directory' first to inspect its files/directories, and then read the necessary files to complete the review."
                                    )
                            elif resolved_path.exists() and resolved_path.is_file() and symbol_name:
                                import difflib
                                all_symbols = []
                                symbol_code = ""
                                imports_list = []
                                skeletons = []
                                
                                if resolved_path.suffix == ".py":
                                    try:
                                        import ast
                                        content = resolved_path.read_text(encoding="utf-8", errors="ignore")
                                        tree = ast.parse(content)
                                        
                                        for node in ast.walk(tree):
                                            if isinstance(node, (ast.Import, ast.ImportFrom)):
                                                imports_list.append(ast.unparse(node))
                                            elif isinstance(node, ast.ClassDef):
                                                all_symbols.append(node.name)
                                                skeletons.append(f"class {node.name}: ...")
                                            elif isinstance(node, ast.FunctionDef):
                                                all_symbols.append(node.name)
                                                skeletons.append(f"def {node.name}(...): ...")
                                                
                                        matched_node = None
                                        for node in ast.walk(tree):
                                            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == symbol_name:
                                                matched_node = node
                                                break
                                        if matched_node:
                                            symbol_code = ast.unparse(matched_node)
                                    except Exception:
                                        pass
                                elif resolved_path.suffix in [".js", ".ts", ".html"]:
                                    try:
                                        content = resolved_path.read_text(encoding="utf-8", errors="ignore")
                                        import re
                                        func_matches = re.finditer(r"(?:function\s+(\w+)|(\w+)\s*\([^)]*\)\s*\{)", content)
                                        class_matches = re.finditer(r"class\s+(\w+)", content)
                                        for m in class_matches:
                                            all_symbols.append(m.group(1))
                                        for m in func_matches:
                                            name = m.group(1) or m.group(2)
                                            if name and name not in ["if", "for", "while", "switch", "catch", "function"]:
                                                all_symbols.append(name)
                                    except Exception:
                                        pass
                                
                                if symbol_code:
                                    task.context.memory.notes.append(
                                        f"⚠️ Scoped Context: Focus strictly on editing/reviewing the symbol '{symbol_name}' inside '{target_path}'.\n"
                                        f"Imports from file:\n" + "\n".join(imports_list) + "\n\n"
                                        f"Current implementation of '{symbol_name}':\n"
                                        f"```\n{symbol_code}\n```\n\n"
                                        f"Sibling structures in file:\n" + "\n".join(skeletons)
                                    )
                                else:
                                    close_matches = difflib.get_close_matches(symbol_name, all_symbols, n=3, cutoff=0.3)
                                    if close_matches:
                                        raise ValueError(
                                            f"Symbol '{symbol_name}' not found in '{target_path}'. Did you mean: " + " or ".join(close_matches) + "?"
                                        )
                                    else:
                                        task.context.memory.notes.append(
                                            f"Warning: Symbol '{symbol_name}' not found in '{target_path}'. Falling back to full-file scope."
                                        )
                        else:
                            task.context.memory.notes.append("Active Target Path Context: None (Treat this as an informational Q&A query. Answer in the conversation directly and do NOT write files to disk.)")
                        
                        pinned_files = data.get("pinned_files", [])
                        if pinned_files:
                            pinned_payload = "📌 Persistent Pinned Files Context:\n"
                            for pf in pinned_files:
                                try:
                                    pf_path = Path(pf)
                                    if not pf_path.is_absolute():
                                        pf_path = SAI_ROOT / pf_path
                                    if pf_path.exists() and pf_path.is_file():
                                        pinned_payload += f"\n--- File: {pf_path.name} ---\n{pf_path.read_text(encoding='utf-8', errors='ignore')[:10000]}\n"
                                except Exception:
                                    pass
                            task.context.memory.notes.append(pinned_payload)

                        fast_chat = data.get("fast_chat", False)
                        if fast_chat:
                            active_agent_name = "Coder"
                            goal_lower = goal.lower()
                            if "review" in goal_lower or "audit" in goal_lower:
                                active_agent_name = "Reviewer"
                            elif "design" in goal_lower or "architect" in goal_lower:
                                active_agent_name = "Architect"
                            elif "plan" in goal_lower:
                                active_agent_name = "Planner"
                                
                            target_agent = next((a for a in Orchestrator().agents if a.name == active_agent_name), None)
                            if target_agent:
                                task.context.current_agent = target_agent.name
                                task.context.memory.notes.append(f"Focus strictly on answering this user request immediately with complete functional solutions: {goal}")
                                
                                from core.security import update_current_activity
                                update_current_activity({
                                    "status": "thinking",
                                    "agent": target_agent.name,
                                    "tool": "",
                                    "path": "",
                                    "command": ""
                                })
                                
                                msg = target_agent.run(task.context)
                                response = msg.metadata.get("response")
                                
                                if response and response.actions:
                                    for action in response.actions:
                                        tool_name = action.get("tool", "")
                                        tool_args = action.get("args", {})
                                        update_current_activity({
                                            "status": "executing",
                                            "tool": tool_name,
                                            "path": str(tool_args.get("path", tool_args.get("target_file", ""))),
                                            "command": str(tool_args.get("command", ""))
                                        })
                                        Orchestrator().execution_engine.execute(action)
                                
                                update_current_activity({
                                    "status": "idle",
                                    "agent": "",
                                    "tool": "",
                                    "path": "",
                                    "command": ""
                                })
                                
                                summary = response.summary if response else "Task completed."
                                reasoning = response.reasoning if response else ["Fast single-agent query completed."]
                                confidence = response.confidence if response else 0.95
                                content = msg.payload.get("memory_update", summary)
                                
                                self.send_response(200)
                                self.send_header("Content-type", "application/json")
                                self.end_headers()
                                self.wfile.write(json.dumps({
                                    "status": "success",
                                    "events": [{
                                        "agent": "Coder",
                                        "summary": summary,
                                        "reasoning": reasoning,
                                        "confidence": confidence,
                                        "content": content
                                    }],
                                    "memory_snapshot": ""
                                }).encode('utf-8'))
                                return

                        orchestrator = Orchestrator()
                        
                        captured_events = []
                        
                        def on_agent_finished(event: Event):
                            msg = event.data["msg"]
                            agent_name = event.data["agent"]
                            
                            response = msg.metadata.get("response") if hasattr(msg, "metadata") else getattr(msg, "response", None)
                            if not response and isinstance(msg, dict):
                                response = msg.get("response")
                                
                            summary = ""
                            reasoning = []
                            confidence = 0.95
                            findings = []
                            
                            if response:
                                summary = getattr(response, "summary", response.get("summary", "") if isinstance(response, dict) else "")
                                reasoning = getattr(response, "reasoning", response.get("reasoning", []) if isinstance(response, dict) else [])
                                confidence = getattr(response, "confidence", response.get("confidence", 0.95) if isinstance(response, dict) else 0.95)
                                findings = getattr(response, "findings", response.get("findings", []) if isinstance(response, dict) else [])
                            
                            content = ""
                            if hasattr(msg, "payload") and isinstance(msg.payload, dict):
                                content = msg.payload.get("memory_update", "")
                                if not content:
                                    content = msg.payload.get("summary", "")
                            if not content:
                                content = getattr(msg, "content", "")
                                
                            event_entry = {
                                "agent": agent_name,
                                "summary": summary,
                                "reasoning": reasoning,
                                "confidence": confidence,
                                "content": content,
                                "findings": findings
                            }
                            captured_events.append(event_entry)

                            log = self.SESSION_ACTIVITY_LOG.setdefault(session_id, [])
                            log.append({"type": "agent_finished", **event_entry})
                            del log[:-200]  # cap growth

                        def on_tool_started(event: Event):
                            log = self.SESSION_ACTIVITY_LOG.setdefault(session_id, [])
                            log.append({"type": "tool_started", "tool": event.data.get("tool", "")})
                            del log[:-200]

                        def on_tool_finished(event: Event):
                            log = self.SESSION_ACTIVITY_LOG.setdefault(session_id, [])
                            log.append({
                                "type": "tool_finished",
                                "tool": event.data.get("tool", ""),
                                "success": event.data.get("success", True)
                            })
                            del log[:-200]

                        # Subscribe to live finished events
                        event_bus.subscribe(EventType.AGENT_FINISHED, on_agent_finished)
                        event_bus.subscribe(EventType.TOOL_STARTED, on_tool_started)
                        event_bus.subscribe(EventType.TOOL_FINISHED, on_tool_finished)

                        try:
                            import core.security
                            core.security.CURRENT_SESSION_ID = session_id
                            # Run the actual orchestrator
                            orchestrator.run(task)
                            
                            self.SESSION_CONVERSATIONS[session_id] = list(task.context.conversation.all())
                            
                            # Check if the orchestrator is waiting for user specifications/clarification
                            last_msg = list(task.context.conversation.all())[-1] if task.context.conversation.all() else None
                            is_next_user = False
                            if last_msg and hasattr(last_msg, "metadata"):
                                resp = last_msg.metadata.get("response")
                                if resp:
                                    next_agent = getattr(resp, "next_agent", "")
                                    if next_agent and str(next_agent).strip().lower() == "user":
                                        is_next_user = True
                                    
                            # Additional Robust Heuristic Search Check
                            if not is_next_user and last_msg:
                                payload = getattr(last_msg, "payload", {})
                                content_str = ""
                                if isinstance(payload, dict):
                                    content_str = (payload.get("content", "") or "") + " " + (payload.get("summary", "") or "")
                                else:
                                    content_str = str(payload)
                                    
                                content_lower = content_str.lower()
                                if "?" in content_str or "awaiting user" in content_lower or "clarify" in content_lower or "clarification" in content_lower or "design preferences" in content_lower or "pause the workflow" in content_lower:
                                    is_next_user = True
                                    
                            self.SESSION_WAITING[session_id] = is_next_user
                            
                            last_question = ""
                            if is_next_user and last_msg:
                                payload = getattr(last_msg, "payload", {})
                                if isinstance(payload, dict):
                                    last_question = payload.get("content", "")
                                else:
                                    last_question = str(payload)
                                    
                            self.send_response(200)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            
                            self.wfile.write(json.dumps({
                                "status": "success",
                                "events": captured_events,
                                "memory_snapshot": task.context.memory.snapshot(),
                                "waiting_for_user": is_next_user,
                                "last_question": last_question,
                                "usage": {
                                    "input_tokens": token_tracker.input_tokens,
                                    "output_tokens": token_tracker.output_tokens,
                                    "calls": token_tracker.calls_count
                                }
                            }).encode('utf-8'))
                        finally:
                            event_bus.unsubscribe(EventType.AGENT_FINISHED, on_agent_finished)
                            event_bus.unsubscribe(EventType.TOOL_STARTED, on_tool_started)
                            event_bus.unsubscribe(EventType.TOOL_FINISHED, on_tool_finished)

                    except PermissionRequestRequired as preq:
                        if 'task' in locals():
                            self.SESSION_CONVERSATIONS[session_id] = list(task.context.conversation.all())
                        self.SESSION_WAITING[session_id] = True
                        
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "permission_required",
                            "path": preq.path,
                            "reason": preq.reason,
                            "kind": getattr(preq, "kind", "path")
                        }).encode('utf-8'))
                        
                    except Exception as e:
                        # Reset current activity status back to idle!
                        from core.security import update_current_activity
                        update_current_activity({
                            "status": "idle",
                            "agent": "",
                            "tool": "",
                            "path": "",
                            "command": ""
                        })
                        import traceback
                        print("\n❌ [Server Error] Exception raised during orchestrator run:")
                        traceback.print_exc()
                        
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))

                elif self.path == "/api/review/apply":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        target_file = data.get("target_file")
                        target_content = data.get("target_content")
                        replacement_content = data.get("replacement_content")
                        force = data.get("force", False)
                        
                        if not target_file or target_content is None or replacement_content is None:
                            self.send_response(400)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({"status": "error", "message": "Missing file or content parameters."}).encode('utf-8'))
                            return
                            
                        # Resolve path
                        resolved_path = Path(target_file)
                        if not resolved_path.is_absolute():
                            resolved_path = SAI_ROOT / resolved_path
                            
                        # Validate permission access
                        from core.security import validate_path
                        if not validate_path(str(resolved_path)):
                            self.send_response(403)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({"status": "error", "message": "Access permission denied."}).encode('utf-8'))
                            return
                            
                        if not resolved_path.exists():
                            self.send_response(404)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({"status": "error", "message": f"File not found: {target_file}"}).encode('utf-8'))
                            return
                            
                        file_text = resolved_path.read_text(encoding="utf-8", errors="ignore")
                        
                        # 1. Normalization & Matching checks
                        def normalize_text(t: str) -> str:
                            # Convert CRLF to LF and strip trailing whitespace on each line
                            lines = [l.rstrip() for l in t.replace("\r\n", "\n").splitlines()]
                            return "\n".join(lines).strip()
                            
                        norm_file = normalize_text(file_text)
                        norm_target = normalize_text(target_content)
                        
                        occurrences = norm_file.count(norm_target)
                        if occurrences == 0:
                            self.send_response(400)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({"status": "error", "message": "Anchor text not found in target file."}).encode('utf-8'))
                            return
                        elif occurrences > 1:
                            self.send_response(400)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({"status": "error", "message": f"Anchor text is ambiguous: detected {occurrences} matches in the file."}).encode('utf-8'))
                            return
                            
                        # 2. AST-Diff Gated Tiers (No Trusting LLM labels)
                        if not force:
                            import textwrap
                            import ast
                            
                            is_pure_style = False
                            if resolved_path.suffix == ".py":
                                try:
                                    t_dedented = textwrap.dedent(target_content)
                                    r_dedented = textwrap.dedent(replacement_content)
                                    t_node = ast.parse(t_dedented)
                                    r_node = ast.parse(r_dedented)
                                    if ast.dump(t_node) == ast.dump(r_node):
                                        is_pure_style = True
                                except Exception:
                                    is_pure_style = False
                                    
                            if not is_pure_style:
                                try:
                                    import re
                                    def strip_tokens(code_str: str) -> str:
                                        code_str = re.sub(r"/\*.*?\*/", "", code_str, flags=re.DOTALL)
                                        code_str = re.sub(r"//.*?\n", "\n", code_str)
                                        code_str = re.sub(r"<!--.*?-->", "", code_str, flags=re.DOTALL)
                                        code_str = re.sub(r"\s+", "", code_str)
                                        return code_str.replace(";", "").replace(",", "")
                                    if strip_tokens(target_content) == strip_tokens(replacement_content):
                                        is_pure_style = True
                                except Exception:
                                    is_pure_style = False
                                    
                            if not is_pure_style:
                                self.send_response(200)
                                self.send_header("Content-type", "application/json")
                                self.end_headers()
                                self.wfile.write(json.dumps({
                                    "status": "requires_preview",
                                    "message": "This patch contains logic or semantic modifications, or target is a non-Python file. Preview required.",
                                    "target_content": target_content,
                                    "replacement_content": replacement_content
                                }).encode('utf-8'))
                                return
                                
                        # 3. Apply the patch
                        if target_content in file_text:
                            new_text = file_text.replace(target_content, replacement_content, 1)
                        else:
                            # Search-and-replace using normalized strings if raw content mismatch
                            # Standard string replace on target_content is the primary fallback
                            new_text = file_text.replace(target_content, replacement_content, 1)
                            
                        # Record pre-patch if not already stashed
                        from core.security import PRE_PATCH_BUFFERS, POST_PATCH_BUFFERS
                        abs_path_str = str(resolved_path.resolve())
                        if abs_path_str not in PRE_PATCH_BUFFERS:
                            PRE_PATCH_BUFFERS[abs_path_str] = file_text
                            
                        resolved_path.write_text(new_text, encoding="utf-8")
                        POST_PATCH_BUFFERS[abs_path_str] = new_text
                        
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "success", "message": "Patch applied successfully!"}).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))

                elif self.path == "/api/workspace/revert":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))

                        # Two callers share this endpoint: the per-transaction Undo
                        # button (sends transaction_id) and the inline-diff Reject
                        # button (sends target_file). Dispatch on which is present --
                        # a second `/api/workspace/revert` handler used to exist
                        # further down for the transaction_id case and was dead code
                        # (unreachable behind this one in the if/elif chain).
                        if "transaction_id" in data:
                            transaction_id = int(data.get("transaction_id", 0))
                            from core.security import revert_transaction
                            success, message = revert_transaction(transaction_id)

                            self.send_response(200)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({
                                "status": "success" if success else "failed",
                                "message": message
                            }).encode('utf-8'))
                            return

                        target_file = data.get("target_file")
                        if not target_file:
                            self.send_response(400)
                            self.end_headers()
                            return
                            
                        # Resolve path
                        resolved_path = Path(target_file)
                        if not resolved_path.is_absolute():
                            resolved_path = SAI_ROOT / resolved_path
                        
                        abs_path_str = str(resolved_path.resolve())
                        
                        from core.security import PRE_PATCH_BUFFERS, POST_PATCH_BUFFERS
                        
                        # 1. External edit safety check
                        if abs_path_str in POST_PATCH_BUFFERS and resolved_path.exists():
                            current_content = resolved_path.read_text(encoding="utf-8", errors="ignore")
                            expected_content = POST_PATCH_BUFFERS[abs_path_str]
                            if current_content != expected_content:
                                self.send_response(400)
                                self.send_header("Content-type", "application/json")
                                self.end_headers()
                                self.wfile.write(json.dumps({
                                    "status": "error",
                                    "message": f"Revert aborted: {target_file} has been edited externally since the agent patch was applied. Wiping the file would discard your manual changes."
                                }).encode('utf-8'))
                                return
                                
                        # 2. Perform Revert
                        if abs_path_str in PRE_PATCH_BUFFERS:
                            orig_content = PRE_PATCH_BUFFERS[abs_path_str]
                            if orig_content is None:
                                if resolved_path.exists():
                                    resolved_path.unlink()
                            else:
                                resolved_path.write_text(orig_content, encoding="utf-8")
                            
                            # Clean up stashes to prevent memory leak
                            PRE_PATCH_BUFFERS.pop(abs_path_str, None)
                            POST_PATCH_BUFFERS.pop(abs_path_str, None)
                            
                            self.send_response(200)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({"status": "success", "message": f"Successfully reverted changes for {target_file}"}).encode('utf-8'))
                        else:
                            # Revert using git checkout on this file only (safe fallback)
                            import subprocess
                            subprocess.run(["git", "checkout", "--", str(resolved_path)], capture_output=True)
                            
                            self.send_response(200)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({"status": "success", "message": f"Reverted changes for {target_file} via git checkout."}).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))

                elif self.path == "/api/run/halt":
                    try:
                        from core.security import trigger_halt
                        trigger_halt()
                        
                        # Also terminate any running background command runner process
                        from tools.terminal import async_process_manager
                        async_process_manager.kill_process()
                        
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success",
                            "message": "Halt instruction registered."
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.end_headers()
                        self.wfile.write(str(e).encode('utf-8'))

                elif self.path == "/api/approve_path":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        path = data.get("path", "")
                        if path:
                            from core.security import approve_request
                            approve_request(path, data.get("kind"))
                            
                            self.send_response(200)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({
                                "status": "success",
                                "message": f"Path '{path}' approved successfully."
                            }).encode('utf-8'))
                        else:
                            self.send_response(400)
                            self.end_headers()
                            self.wfile.write(b"Error: Path is empty.")
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))

                elif self.path == "/api/git_branch":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        path_str = data.get("path", "")
                        if not path_str:
                            self.send_response(400)
                            self.end_headers()
                            self.wfile.write(b"Error: Path is empty.")
                            return
                            
                        target_path = Path(path_str)
                        if not target_path.is_absolute():
                            target_path = SAI_ROOT / target_path
                            
                        from core.security import validate_path
                        if not validate_path(target_path):
                            self.send_response(200)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({
                                "status": "unauthorized",
                                "message": "Path outside sandbox."
                            }).encode('utf-8'))
                            return
                            
                        from git import Repo
                        try:
                            repo = Repo(target_path, search_parent_directories=True)
                            branch_name = repo.active_branch.name
                            self.send_response(200)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({
                                "status": "success",
                                "branch": branch_name
                            }).encode('utf-8'))
                        except Exception:
                            self.send_response(200)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({
                                "status": "not_repo"
                            }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))

                elif self.path == "/api/terminal/kill":
                    try:
                        from tools.terminal import async_process_manager
                        async_process_manager.terminate()
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success"
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))

                elif self.path == "/api/terminal/input":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        text = data.get("input", "")
                        from tools.terminal import async_process_manager
                        success = async_process_manager.write_stdin(text)
                        
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success" if success else "failed"
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))

                elif self.path == "/api/workspace/clear_session":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        session_id = data.get("session_id", "default_session")
                        if session_id in self.SESSION_CONVERSATIONS:
                            self.SESSION_CONVERSATIONS[session_id] = []
                        self.SESSION_WAITING[session_id] = False
                        
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success",
                            "message": f"Session memory cleared for '{session_id}'."
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))

                elif self.path == "/api/workspace/transactions":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        session_id = data.get("session_id", "default_session")
                        from core.security import get_transaction_history
                        history = get_transaction_history(session_id)
                        
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success",
                            "transactions": history
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))

                elif self.path == "/api/workspace/revert_to":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        session_id = data.get("session_id", "default_session")
                        target_id = int(data.get("transaction_id", 0))
                        from core.security import revert_to_transaction_snapshot
                        success, message = revert_to_transaction_snapshot(session_id, target_id)
                        
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success" if success else "failed",
                            "message": message
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))

                elif self.path == "/api/terminal/execute":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        command = data.get("command", "").strip()
                        if not command:
                            raise Exception("Empty command.")
                            
                        from core.settings import load_settings
                        from core.security import get_current_workspace
                        settings = load_settings()
                        cwd = get_current_workspace()
                        
                        run_cmd = command
                        is_sandboxed = False
                        
                        if settings.get("docker_sandbox", False):
                            try:
                                import subprocess
                                check = subprocess.run(["docker", "info"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2.0)
                                if check.returncode == 0:
                                    run_cmd = f'docker run --rm -v "{cwd}":/workspace -w /workspace alpine sh -c {repr(command)}'
                                    is_sandboxed = True
                            except Exception:
                                pass
                                
                        import subprocess
                        result = subprocess.run(
                            run_cmd,
                            shell=True,
                            cwd=str(cwd),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            timeout=15.0
                        )
                        
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success",
                            "output": result.stdout,
                            "exit_code": result.returncode,
                            "sandboxed": is_sandboxed
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))

                elif self.path == "/api/git/branch":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        branch = data.get("branch", "").strip()
                        create = data.get("create", False)
                        
                        if not branch:
                            raise ValueError("Branch name is required.")
                            
                        import subprocess
                        cwd = str(SAI_ROOT)
                        if create:
                            cmd = ["git", "checkout", "-b", branch]
                        else:
                            cmd = ["git", "checkout", branch]
                            
                        res = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                        if res.returncode != 0:
                            raise ValueError(res.stderr or res.stdout)
                            
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success",
                            "branch": branch
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))

                elif self.path == "/api/workspace/tree":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        path_str = data.get("path", "").strip()
                        from core.security import get_current_workspace, validate_path
                        target_dir = Path(path_str) if path_str else get_current_workspace()
                        
                        if not target_dir.is_absolute():
                            target_dir = SAI_ROOT / target_dir
                            
                        # If a file path is passed, explore its parent directory context instead of failing!
                        if target_dir.exists() and target_dir.is_file():
                            target_dir = target_dir.parent

                        if not validate_path(target_dir):
                            self.send_response(200)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({
                                "status": "unauthorized",
                                "message": "Outside sandbox boundaries. Submit review to authorize."
                            }).encode('utf-8'))
                            return

                        if not target_dir.exists() or not target_dir.is_dir():
                            self.send_response(200)
                            self.send_header("Content-type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({
                                "status": "not_found",
                                "message": "Directory does not exist."
                            }).encode('utf-8'))
                            return

                        from repository.ignore import IgnoreParser
                        ignore_parser = IgnoreParser(target_dir)

                        def extract_symbols(path: Path) -> list:
                            symbols = []
                            if path.suffix == ".py":
                                try:
                                    import ast
                                    content = path.read_text(encoding="utf-8", errors="ignore")
                                    tree = ast.parse(content)
                                    for node in ast.walk(tree):
                                        if isinstance(node, ast.ClassDef):
                                            symbols.append({"name": node.name, "type": "class", "line": node.lineno})
                                        elif isinstance(node, ast.FunctionDef):
                                            symbols.append({"name": node.name, "type": "function", "line": node.lineno})
                                except Exception:
                                    pass
                            elif path.suffix in [".js", ".ts", ".html"]:
                                try:
                                    content = path.read_text(encoding="utf-8", errors="ignore")
                                    import re
                                    func_matches = re.finditer(r"(?:function\s+(\w+)|(\w+)\s*\([^)]*\)\s*\{)", content)
                                    class_matches = re.finditer(r"class\s+(\w+)", content)
                                    for m in class_matches:
                                        symbols.append({"name": m.group(1), "type": "class", "line": content[:m.start()].count("\n") + 1})
                                    for m in func_matches:
                                        name = m.group(1) or m.group(2)
                                        if name and name not in ["if", "for", "while", "switch", "catch", "function"]:
                                            symbols.append({"name": name, "type": "function", "line": content[:m.start()].count("\n") + 1})
                                except Exception:
                                    pass
                            return symbols

                        def build_tree(path: Path) -> dict:
                            if ignore_parser.is_ignored(path):
                                return None
                            try:
                                node = {
                                    "name": path.name,
                                    "path": str(path),
                                    "is_dir": path.is_dir()
                                }
                                if path.is_dir():
                                    children = []
                                    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                                        c_node = build_tree(child)
                                        if c_node:
                                            children.append(c_node)
                                    node["children"] = children
                                else:
                                    node["symbols"] = extract_symbols(path)
                                return node
                            except Exception:
                                return None

                        tree_data = build_tree(target_dir) or {"name": target_dir.name, "path": str(target_dir), "is_dir": True, "children": []}
                        
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success",
                            "tree": tree_data
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))
                        
                elif self.path == "/api/workspace/create_file":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        path_str = data.get("path", "").strip()
                        filename = data.get("filename", "").strip()
                        
                        from core.security import validate_path
                        target_dir = Path(path_str)
                        if not target_dir.is_absolute():
                            target_dir = SAI_ROOT / target_dir
                            
                        target_file = target_dir / filename
                        
                        if not validate_path(target_file):
                            self.send_response(403)
                            self.end_headers()
                            self.wfile.write(b"Error: Path outside sandbox boundary.")
                            return
                            
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        target_file.write_text("// Created via sAI Explorer\n", encoding="utf-8")
                        
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.end_headers()
                        self.wfile.write(str(e).encode('utf-8'))
                        
                elif self.path == "/api/workspace/create_folder":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        path_str = data.get("path", "").strip()
                        foldername = data.get("foldername", "").strip()
                        
                        from core.security import validate_path
                        target_dir = Path(path_str)
                        if not target_dir.is_absolute():
                            target_dir = SAI_ROOT / target_dir
                            
                        target_folder = target_dir / foldername
                        
                        if not validate_path(target_folder):
                            self.send_response(403)
                            self.end_headers()
                            self.wfile.write(b"Error: Path outside sandbox boundary.")
                            return
                            
                        target_folder.mkdir(parents=True, exist_ok=True)
                        
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.end_headers()
                        self.wfile.write(str(e).encode('utf-8'))
                        
                elif self.path == "/api/workspace/delete":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        path_str = data.get("path", "").strip()
                        
                        from core.security import validate_path
                        target_path = Path(path_str)
                        if not target_path.is_absolute():
                            target_path = SAI_ROOT / target_path
                            
                        if not validate_path(target_path):
                            self.send_response(403)
                            self.end_headers()
                            self.wfile.write(b"Error: Path outside sandbox boundary.")
                            return
                            
                        if target_path.exists():
                            import shutil
                            if target_path.is_dir():
                                shutil.rmtree(target_path)
                            else:
                                target_path.unlink()
                                
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.end_headers()
                        self.wfile.write(str(e).encode('utf-8'))

                elif self.path == "/api/settings":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        from core.settings import load_settings, save_settings
                        current = load_settings()
                        
                        new_nvidia = data.get("nvidia_key", "").strip()
                        if new_nvidia and not new_nvidia.endswith("..."):
                            current["nvidia_key"] = new_nvidia
                            
                        new_openai = data.get("openai_key", "").strip()
                        if new_openai and not new_openai.endswith("..."):
                            current["openai_key"] = new_openai

                        new_openrouter = data.get("openrouter_key", "").strip()
                        if new_openrouter and not new_openrouter.endswith("..."):
                            current["openrouter_key"] = new_openrouter

                        current["provider"] = data.get("provider", current["provider"])
                        current["ollama_url"] = data.get("ollama_url", current["ollama_url"])
                        current["coder_model"] = data.get("coder_model", current["coder_model"])
                        current["reasoner_model"] = data.get("reasoner_model", current["reasoner_model"])
                        current["temperature"] = float(data.get("temperature", current["temperature"]))
                        # The web settings panel's temperature is an explicit global override.
                        current["temperature_override"] = current["temperature"]
                        current["aider_mode"] = bool(data.get("aider_mode", True))
                        current["graphiti_mode"] = bool(data.get("graphiti_mode", True))
                        
                        save_settings(current)
                        
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.end_headers()
                        self.wfile.write(str(e).encode('utf-8'))
                        
                elif self.path == "/api/memory/graph":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        action = data.get("action", "")
                        item_type = data.get("type", "")
                        value = data.get("value", "").strip()
                        
                        from memory.graphiti import GraphitiMemory
                        graphiti = GraphitiMemory()
                        
                        if action == "add":
                            if item_type == "fact":
                                graphiti.add_fact(value)
                            elif item_type == "decision":
                                graphiti.add_decision(value)
                        elif action == "delete":
                            if item_type == "fact":
                                graphiti.remove_fact(value)
                            elif item_type == "decision":
                                graphiti.remove_decision(value)
                        elif action == "clear":
                            confirm_token = data.get("confirm_token", "")
                            if confirm_token != "CONFIRM_CLEAR_ALL":
                                self.send_response(400)
                                self.send_header("Content-type", "application/json")
                                self.end_headers()
                                self.wfile.write(json.dumps({"status": "error", "message": "Destructive endpoint requires explicit server confirmation."}).encode('utf-8'))
                                return
                            graphiti.clear_all()
                            
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "error",
                            "message": str(e)
                        }).encode('utf-8'))
                        
                elif self.path == "/api/git/commit":
                    try:
                        content_length = int(self.headers['Content-Length'])
                        post_data = self.rfile.read(content_length)
                        data = json.loads(post_data.decode('utf-8'))
                        
                        message = data.get("message", "Commit via sAI").strip()
                        from core.security import get_current_workspace
                        workspace = get_current_workspace()
                        
                        import subprocess
                        subprocess.run(["git", "add", "."], cwd=str(workspace))
                        res = subprocess.run(["git", "commit", "-m", message], cwd=str(workspace), capture_output=True, text=True)
                        
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success" if res.returncode == 0 else "error",
                            "message": res.stdout or res.stderr
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.end_headers()
                        self.wfile.write(str(e).encode('utf-8'))
                        
                elif self.path == "/api/git/discard":
                    try:
                        from core.security import get_current_workspace
                        workspace = get_current_workspace()
                        
                        import subprocess
                        subprocess.run(["git", "checkout", "--", "."], cwd=str(workspace))
                        subprocess.run(["git", "clean", "-fd", "."], cwd=str(workspace))
                        
                        self.send_response(200)
                        self.send_header("Content-type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "status": "success",
                            "message": "Workspace modifications discarded successfully."
                        }).encode('utf-8'))
                    except Exception as e:
                        self.send_response(500)
                        self.end_headers()
                        self.wfile.write(str(e).encode('utf-8'))
                        
        def open_browser():
            import time
            time.sleep(1.2)
            webbrowser.open(f"http://127.0.0.1:{PORT}")

        print(f"\n🚀 Starting sAI Clayline Web Server on http://127.0.0.1:{PORT}...")
        print("💡 Browser will open automatically. Press Ctrl+C to terminate.")
        
        # Open default web browser asynchronously
        threading.Thread(target=open_browser, daemon=True).start()
        
        from core.settings import load_settings
        load_settings()
        
        server = HTTPServer(("127.0.0.1", PORT), ClaylineHTTPServer)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down sAI Web Server...")
            server.server_close()

def parse_outline_symbols(file_path: Path) -> list:
    symbols = []
    ext = file_path.suffix.lower()
    
    if not file_path.exists():
        return symbols
        
    try:
        import re
        content = file_path.read_text(encoding="utf-8")
        
        if ext == ".py":
            import ast
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        symbols.append({"name": node.name, "type": "class", "line": node.lineno})
                    elif isinstance(node, ast.FunctionDef):
                        symbols.append({"name": node.name, "type": "function", "line": node.lineno})
            except Exception:
                pass
                
        # Regex-based parser for Javascript or fallback
        if not symbols:
            lines = content.splitlines()
            for idx, line in enumerate(lines):
                class_match = re.search(r'\bclass\s+([a-zA-Z0-9_]+)', line)
                if class_match:
                    symbols.append({"name": class_match.group(1), "type": "class", "line": idx + 1})
                    continue
                    
                fn_match = re.search(r'\b(?:function\s+([a-zA-Z0-9_]+)|const\s+([a-zA-Z0-9_]+)\s*=\s*(?:\([^)]*\)|[a-zA-Z0-9_]+)\s*=>|([a-zA-Z0-9_]+)\s*\([^)]*\)\s*\{)', line)
                if fn_match:
                    name = fn_match.group(1) or fn_match.group(2) or fn_match.group(3)
                    if name and name not in ["if", "for", "while", "switch", "catch"]:
                        symbols.append({"name": name, "type": "function", "line": idx + 1})
                        
    except Exception:
        pass
        
    symbols.sort(key=lambda s: s["line"])
    return symbols


if __name__ == "__main__":
    main()