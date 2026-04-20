# /rtclose — Session Close

This command closes the current session and performs:
1. JSONL export (Python pipeline)
2. Entity extraction (knowledge graph feeding)
3. Handoff + MEMORY.md update
4. Report

## Argument

Optional title: `/rtclose "Session Title"`
If not provided, generate a title from the session's main topic.

---

## Step 0: Project Detection

Detect the project name from CWD and create a project tag:

```bash
# Get CWD
PROJECT_DIR=$(basename "$(pwd)")
PROJECT_TAG="project/$(echo "$PROJECT_DIR" | tr '[:upper:]' '[:lower:]')"
```

**JSONL path is CWD-specific:** Each project's JSONLs are under different encoded paths.
- Encode the CWD path: replace `/` with `-`, remove `:` — look under `~/.claude/projects/`

**Export always goes to the vault:** `vault/` relative to the project root.

## Step 1: JSONL Export (Python pipeline)

Find the session JSONL file and export:

```bash
# Detect project directory
PROJECT_DIR=$(basename "$(pwd)")
PROJECT_TAG="project/$(echo "$PROJECT_DIR" | tr '[:upper:]' '[:lower:]')"

# Find JSONL path based on CWD
# Create encoded CWD: /home/user/projects/myproject -> home-user-projects-myproject
CWD_ENCODED=$(pwd | sed 's|^/||' | sed 's|/|-|g')
JSONL_PATH=$(ls -t ~/.claude/projects/${CWD_ENCODED}/*.jsonl 2>/dev/null | head -1)

# If not found, search manually
if [ -z "$JSONL_PATH" ]; then
  echo "WARNING: JSONL not found, searching ~/.claude/projects/ ..."
  JSONL_PATH=$(find ~/.claude/projects/ -name "*.jsonl" -newer /tmp/session_start 2>/dev/null | head -1)
fi

# Export — to vault relative to project root
PYTHONUTF8=1 rawthink-export "$JSONL_PATH" \
  --title "[TITLE]" \
  --slug "[SLUG]" \
  --tags "$PROJECT_TAG,[OTHER_TAGS]" \
  --vault-dir vault \
  --reindex
```

- If the user provided a title via `/rtclose "Session Title"`, use it for `[TITLE]`
- Otherwise generate a title from the session's main topic (2-5 words)
- `[SLUG]` = slugify the title (lowercase, hyphen-separated)
- `[OTHER_TAGS]` = extract from session content (comma-separated) — `$PROJECT_TAG` is added automatically
- `--reindex` flag updates Qdrant. If Ollama is down or errors, retry without `--reindex`.
- Parse the output as JSON — save `session_id` and `files` fields.

## Step 2: Entity Extraction (Knowledge Graph)

Read the generated clean dialog MD and feed the knowledge graph:

1. Use `read_graph()` MCP tool to get the current graph
2. For new concepts, rules, insights in the session:
   - `create_entities()` to add new entities
   - `create_relations()` to create connections
   - `add_observations()` to add new info to existing entities
3. Note the counts of added entities/relations

**Entity extraction criteria:**
- Repeated or emphasized concepts in the session -> entity
- Formulated rules or decisions -> entity (type: rule/decision)
- Personal insights -> entity (type: insight)
- Analogies and connections -> relation
- Entity names in kebab-case
- **Use project prefix for project-specific entities:** `myproject-performance-fix` vs `general-concept`

**If already exists:** Use `add_observations()` to add new info. Don't create duplicates.

## Step 3: Handoff + MEMORY.md

### Write vault/handoff-{PROJECT_NAME}-{SESSION_ID}.md:

**Multi-terminal safe:** Each session writes its own handoff file — parallel terminals never overwrite each other.

`PROJECT_NAME` = Step 0's `PROJECT_DIR` lowercase
`SESSION_ID` = from Step 1 export output

**IMPORTANT:** Claude Code's Write tool requires reading the file first with Read.

**Steps:**
1. Write directly — no archive step needed (each session has its own file)
2. Write new content with Write tool to `vault/handoff-${PROJECT_NAME}-${SESSION_ID}.md`

```yaml
---
session: "[SESSION_ID]"
date: [DATE]
project: "[PROJECT_TAG]"
---
## Summary
[2-3 sentences]

## Open Questions
- [list]

## Next Steps
- [list]

## Key Insight
- [most important takeaway]
```

### Update MEMORY.md:
File: `~/.claude/projects/<encoded-cwd>/memory/MEMORY.md`

- New concepts/positions -> add as graph entities (DON'T add to MEMORY.md)
- New technical decisions -> add as graph entity, only 1-line summary in MEMORY.md
- New open questions -> add as `open-question-*` entity in graph
- Update changed info (project status, etc.)
- **Stay under 80 lines** — details go to graph, MEMORY.md is essential context + pointers only

## Step 4: Report

When complete, report in this format:

```
Session closed: [SESSION_ID]
Project: [PROJECT_TAG]

Files:
- Clean dialog: vault/sessions/[...]
- Full transcript: vault/archive/raw/[...]
- HTML: vault/outputs/[...]
- PDF: [if available or "skipped"]

Knowledge Graph:
- [N] new entities added
- [N] new relations added
- [N] observations updated

Reindex: [N] chunks indexed (or "skipped")
Handoff: vault/handoff-[PROJECT_NAME]-[SESSION_ID].md written
MEMORY.md: [change summary]
```
