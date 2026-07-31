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

1. `search_nodes(query, limit=10)` for the session's main subjects — check what
   already exists before writing anything
2. Write everything in ONE `record()` call: entities, relations and
   observations together. It validates the whole batch before writing any of
   it, so a half-valid extraction writes nothing rather than half a graph.
3. Note the counts returned

**Every entity needs a role and a subject area. These are separate fields:**

`entityType` — what role the node plays. Closed list, nothing else is accepted:

| type | use for |
|---|---|
| `decision` | a choice made, with alternatives rejected |
| `concept` | an idea, theory, model, analogy |
| `finding` | something discovered or measured — bug, result, audit |
| `rule` | a durable constraint or pattern to follow |
| `open-question` | unresolved, waiting on evidence |
| `artifact` | a project, tool, document, feature, source |
| `insight` | a realisation that changed how something is seen |
| `task` | a unit of intended work |
| `event` | something that happened at a point in time |
| `thing` | a person, object or substance named directly |

`domain` — the subject area: `software`, `music`, `history`, `philosophy`,
`health`, `writing`, `neuro`, `finance`, `personal`, `galaxy`.

Keep them separate. Collapsing "a finding about health" into one type is how a
vocabulary grows one entry per subject until nothing can be queried.

`epistemic` — how strongly it is held: `assertion` (grounds exist),
`hypothesis` (plausible, unverified), `speculation` (entertained), or omit for
`unknown`. **Do not default to `assertion`.** If the session did not establish
it, it is not an assertion.

**Relations** use the canonical vocabulary: `supports`, `contradicts`,
`evolved_into`, `depends_on`, `exemplifies`, `part_of`, `caused_by`, `enables`,
`supersedes`, `related_to`, `investigates`, `informs`, `uses`. Close synonyms
are folded automatically; anything else is rejected. If a connection needs a
more specific description, put it in an observation and use the nearest
canonical type.

**Naming:** kebab-case. Use a project prefix for project-scoped entities
(`myproject-performance-fix` vs `general-concept`).

**If it already exists:** pass it in the same `record()` call with new
observations. Merging is the default; you do not need a different tool.

**If the session changed your mind about something:** use `revise()`, never a
delete tool. Record the new belief first, then revise the old one pointing at
it. An archive that forgets what you used to think cannot answer why you
changed your mind — which is the main reason to keep one.

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
