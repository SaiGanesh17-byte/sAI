# Tool Ecosystem - sAI

This document lists the local tool integrations available to sAI agents and explains how tool calls are executed and validated.

## Core Tool Integrations

### 1. Web Search
* **Driver**: `duckduckgo-search`
* **Purpose**: Fetches real-time information, documentation, and error resolutions from the web without relying on expensive cloud search APIs.

### 2. Code Editing
* **Driver**: `Aider` (integration hooks)
* **Purpose**: Enables precise, AST-aware diff applications to code files. Rather than rewritten files, Aider provides quick, reliable search-and-replace syntax.

### 3. Repository Mapping
* **Drivers**: `tree-sitter`, `tree-sitter-language-pack`, `grep-ast`, `ripgrep`, `fd`
* **Purpose**: Generates high-level structural map summaries of the project repository, resolving where class, function, and variable definitions exist to help agents locate files quickly.

### 4. Terminal Command Execution
* **Driver**: Python `subprocess`, native PTY streams
* **Purpose**: Runs test commands, compilers, and development servers. Execution can run in blocking mode or async background processes.

### 5. Version Control
* **Driver**: `GitPython`
* **Purpose**: Reads git diffs, performs staging/commits, creates branches, and handles rollbacks.

### 6. File Parsing (Comprehensive File Support)
Each format is routed through dedicated parsers for token efficiency and readability:
* **Plain Text / Code**: `txt`, `py`, `js`, `ts`, `java`, `kt`, `go`, `rs`, `cpp`, `css`, `html`, `markdown`, `logs` (standard python reading & AST tokenizers).
* **Documents**: `pdf` (`pypdf`, `pymupdf`), `docx` (`python-docx`), `pptx` (`python-pptx`).
* **Structured Data**: `csv`, `json`, `yaml`, `xml`, `xlsx` (`openpyxl`, `pandas`, `lxml`, `beautifulsoup4`).
* **Archives**: `jar`, `zip`, `tar`, `gz` (decompression libraries to list structures and extract text contents).
* **Databases**: `sqlite`, `db` (SQL queries).
* **Media Assets**: `images` (`pillow` OCR/metadata), `audio`, `video` (metadata extraction).

---

## Tool Orchestration & Validation Flow
Agents cannot invoke tools directly on the user's system. They must post a structured tool request event to the Orchestrator:

```
[Agent] --(Emits Tool Request Event)--> [Orchestrator Validation Engine]
                                                |
                              +-----------------+-----------------+
                              |                                   |
                         (Safe Path)                        (Unsafe/Destructive)
                              |                                   |
                    [Sandbox Execution]                  [Prompt User for Approval]
                              |                                   |
                              v                                   v
                      [Execute Tool]                      [Execute on Approved]
                              |
                              +-------->(Returns Result Event)-------->[Agent]
```

* **Validation Rules**:
  * Prevents folder paths outside the repository workspace from being accessed (unless explicit user authorization is configured).
  * Prompts for user confirmations before running destructive terminal commands (e.g., recursive deletes, port terminations) or committing git resets.
  * Encrypts environment secrets (keys, auth headers) to prevent leaks to command outputs.
